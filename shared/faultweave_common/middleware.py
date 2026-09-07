from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .fault_injection import should_inject_downstream_error
from .logging import (
    LogOutcome,
    configure_logging,
    reset_correlation_context,
    set_correlation_context,
)


class CorrelationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service: str) -> None:
        super().__init__(app)
        self.service = service
        self.logger = configure_logging(service)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        current_run_id = self._clean(request.headers.get("X-Run-ID"), "manual")
        current_request_id = self._clean(request.headers.get("X-Request-ID"), str(uuid4()))
        current_trace_id = self._clean(request.headers.get("X-Trace-ID"), str(uuid4()))
        request.state.run_id = current_run_id
        request.state.request_id = current_request_id
        request.state.trace_id = current_trace_id
        context_tokens = set_correlation_context(
            current_run_id,
            current_request_id,
            current_trace_id,
        )
        started_at = perf_counter()
        try:
            if should_inject_downstream_error(self.service, request.url.path):
                response = JSONResponse({"detail": "Internal service error"}, status_code=500)
            else:
                response = await call_next(request)
            latency_ms = round((perf_counter() - started_at) * 1000, 3)
            level = "info"
            outcome = LogOutcome.SUCCESS
            if response.status_code >= 500:
                level = "error"
                outcome = LogOutcome.FAILURE
            elif response.status_code >= 400:
                level = "warning"
                outcome = LogOutcome.FAILURE
            event_type = (
                "health_check_completed"
                if request.url.path.startswith("/health/")
                else "http_request_completed"
            )
            getattr(self.logger, level)(
                event_type,
                "HTTP request completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=latency_ms,
                outcome=outcome,
            )
            response.headers["X-Run-ID"] = current_run_id
            response.headers["X-Request-ID"] = current_request_id
            response.headers["X-Trace-ID"] = current_trace_id
            return response
        except Exception as exc:
            latency_ms = round((perf_counter() - started_at) * 1000, 3)
            self.logger.error(
                "http_request_failed",
                "HTTP request failed with an unhandled exception",
                method=request.method,
                path=request.url.path,
                status_code=500,
                latency_ms=latency_ms,
                outcome=LogOutcome.FAILURE,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            reset_correlation_context(context_tokens)

    @staticmethod
    def _clean(value: str | None, fallback: str) -> str:
        return (value or fallback).strip()[:100] or fallback


# Retained as an import-compatible alias for Phase 1/2 modules.
RequestIdMiddleware = CorrelationMiddleware


def request_id(request: Request) -> str:
    return request.state.request_id


def run_id(request: Request) -> str:
    return request.state.run_id


def trace_id(request: Request) -> str:
    return request.state.trace_id


def request_correlation_headers(request: Request) -> dict[str, str]:
    return {
        "X-Run-ID": run_id(request),
        "X-Request-ID": request_id(request),
        "X-Trace-ID": trace_id(request),
    }
