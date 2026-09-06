from __future__ import annotations

import json
import logging
import os
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

LOG_SCHEMA_VERSION = "1.1"
run_id_context: ContextVar[str | None] = ContextVar("run_id", default=None)
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_context: ContextVar[str | None] = ContextVar("trace_id", default=None)

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)Bearer\s+[^\s,;]+"),
    re.compile(r"(?i)(password|token|secret|api[_-]?key)=([^\s,;&]+)"),
    re.compile(r"(?i)(postgres(?:ql)?(?:\+asyncpg)?://[^:]+:)([^@\s]+)(@)"),
)


class LogOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


class StructuredLogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = LOG_SCHEMA_VERSION
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    environment: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    run_id: str | None = Field(default=None, max_length=100)
    request_id: str | None = Field(default=None, max_length=100)
    trace_id: str | None = Field(default=None, max_length=100)
    method: str | None = Field(default=None, max_length=10)
    path: str | None = Field(default=None, max_length=300)
    status_code: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float | None = Field(default=None, ge=0)
    outcome: LogOutcome = LogOutcome.UNKNOWN
    success: bool | None = None
    user_id: str | None = Field(default=None, max_length=100)
    transaction_id: str | None = Field(default=None, max_length=100)
    payment_id: str | None = Field(default=None, max_length=100)
    downstream_service: str | None = Field(default=None, max_length=100)
    error_type: str | None = Field(default=None, max_length=150)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value


def set_correlation_context(
    run_id: str,
    request_id: str,
    trace_id: str,
) -> tuple[Token[str | None], Token[str | None], Token[str | None]]:
    return (
        run_id_context.set(run_id),
        request_id_context.set(request_id),
        trace_id_context.set(trace_id),
    )


def reset_correlation_context(
    tokens: tuple[Token[str | None], Token[str | None], Token[str | None]],
) -> None:
    run_id_context.reset(tokens[0])
    request_id_context.reset(tokens[1])
    trace_id_context.reset(tokens[2])


def correlation_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if run_id := run_id_context.get():
        headers["X-Run-ID"] = run_id
    if request_id := request_id_context.get():
        headers["X-Request-ID"] = request_id
    if trace_id := trace_id_context.get():
        headers["X-Trace-ID"] = trace_id
    return headers


def _redact_text(value: str) -> str:
    redacted = value
    redacted = _SECRET_PATTERNS[0].sub("Bearer [REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[1].sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[2].sub(r"\1[REDACTED]\3", redacted)
    return redacted


def redact(value: Any, key: str | None = None) -> Any:
    if key and any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): redact(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, UUID | datetime | Enum):
        return str(value.value if isinstance(value, Enum) else value)
    return value


class StructuredJsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        supplied = dict(getattr(record, "faultweave_event", {}))
        supplied.setdefault("run_id", run_id_context.get())
        supplied.setdefault("request_id", request_id_context.get())
        supplied.setdefault("trace_id", trace_id_context.get())
        outcome = supplied.get("outcome", LogOutcome.UNKNOWN)
        if "success" not in supplied:
            if outcome == LogOutcome.SUCCESS or outcome == LogOutcome.SUCCESS.value:
                supplied["success"] = True
            elif outcome == LogOutcome.FAILURE or outcome == LogOutcome.FAILURE.value:
                supplied["success"] = False
        try:
            event = StructuredLogEvent(
                service=self.service,
                environment=self.environment,
                level=record.levelname,
                message=_redact_text(record.getMessage()),
                **redact(supplied),
            )
        except (ValidationError, TypeError, ValueError) as exc:
            event = StructuredLogEvent(
                service=self.service,
                environment=self.environment,
                level="ERROR",
                event_type="logging_schema_error",
                message="A log event failed schema validation",
                run_id=run_id_context.get(),
                request_id=request_id_context.get(),
                trace_id=trace_id_context.get(),
                outcome=LogOutcome.FAILURE,
                success=False,
                error_type=type(exc).__name__,
            )
        return json.dumps(event.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)


class EventLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, event_type: str, message: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event_type, message, fields)

    def info(self, event_type: str, message: str, **fields: Any) -> None:
        self._emit(logging.INFO, event_type, message, fields)

    def warning(self, event_type: str, message: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event_type, message, fields)

    def error(self, event_type: str, message: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event_type, message, fields)

    def _emit(self, level: int, event_type: str, message: str, fields: dict[str, Any]) -> None:
        self._logger.log(
            level,
            message,
            extra={"faultweave_event": {"event_type": event_type, **fields}},
        )


def configure_logging(service: str) -> EventLogger:
    logger = logging.getLogger(f"faultweave.{service}")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            StructuredJsonFormatter(
                service=service,
                environment=os.getenv("APP_ENV", "development"),
            )
        )
        logger.addHandler(handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    return EventLogger(logger)
