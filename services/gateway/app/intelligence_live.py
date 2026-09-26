from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any

from faultweave_common.logging import StructuredLogEvent
from pydantic import BaseModel, ConfigDict, Field

WINDOW_SECONDS = (10, 60)
SERVICE_NAMES = ("gateway", "authentication", "transaction", "payment", "account", "ledger")
SCENARIO_NAMES = ("valid", "invalid_login", "invalid_account", "invalid_amount")
MAX_BUFFER_SECONDS = 180
INTELLIGENCE_PATH_PREFIX = "/api/v1/intelligence/"


class LiveWindowNotReady(RuntimeError):
    """Raised when the live telemetry watermark has not covered a full model window."""


class LiveTelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[StructuredLogEvent] = Field(min_length=1, max_length=500)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"invalid timestamp {value!r}")
    if parsed.tzinfo is None:
        raise ValueError("telemetry timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return round(math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)), 3)


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _latency_shape(values: list[float], prefix: str) -> dict[str, float]:
    p25 = float(_percentile(values, 0.25) or 0.0)
    p50 = float(_percentile(values, 0.50) or 0.0)
    p95 = float(_percentile(values, 0.95) or 0.0)
    p99 = float(_percentile(values, 0.99) or 0.0)
    maximum = round(max(values), 3) if values else 0.0
    return {
        f"{prefix}_mean_ms": _mean(values),
        f"{prefix}_p50_ms": p50,
        f"{prefix}_p95_ms": p95,
        f"{prefix}_p99_ms": p99,
        f"{prefix}_max_ms": maximum,
        f"{prefix}_std_ms": _std(values),
        f"{prefix}_iqr_ms": round(float(_percentile(values, 0.75) or 0.0) - p25, 3),
        f"{prefix}_max_to_p50_ratio": round(maximum / max(p50, 1e-9), 6),
    }


def request_features(records: list[dict[str, Any]], duration_seconds: float) -> dict[str, float]:
    count = len(records)
    latencies = [
        value for item in records if (value := _numeric(item.get("latency_ms"))) is not None
    ]
    statuses = [item.get("status_code") for item in records]
    non_2xx = sum(status is None or not 200 <= int(status) < 300 for status in statuses)
    scenarios = Counter(str(item.get("scenario")) for item in records)
    latency_shape = _latency_shape(latencies, "request_latency")
    result = {
        "request_count": float(count),
        "request_rate_per_second": round(count / duration_seconds, 6),
        **latency_shape,
        "request_success_rate": _rate(
            sum(item.get("expected_outcome") is True for item in records), count
        ),
        "request_non_2xx_rate": _rate(non_2xx, count),
        "request_client_error_rate": _rate(
            sum(status is not None and 400 <= int(status) < 500 for status in statuses), count
        ),
        "request_server_error_rate": _rate(
            sum(status is not None and 500 <= int(status) < 600 for status in statuses), count
        ),
        "request_transport_error_rate": _rate(
            sum(item.get("transport_error") is not None for item in records), count
        ),
        "request_unexpected_outcome_rate": _rate(
            sum(item.get("expected_outcome") is False for item in records), count
        ),
    }
    result.update(
        {f"request_{name}_rate": _rate(scenarios[name], count) for name in SCENARIO_NAMES}
    )
    return result


def event_features(records: list[dict[str, Any]], duration_seconds: float) -> dict[str, float]:
    count = len(records)
    latencies = [
        value for item in records if (value := _numeric(item.get("latency_ms"))) is not None
    ]
    statuses = [item.get("status_code") for item in records]
    services = Counter(str(item.get("service")) for item in records)
    types = {str(item.get("event_type")) for item in records}
    error_types = [str(item.get("error_type") or "").lower() for item in records]
    service_records = {
        service: [item for item in records if item.get("service") == service]
        for service in SERVICE_NAMES
    }
    downstream_records = {
        service: [item for item in records if item.get("downstream_service") == service]
        for service in SERVICE_NAMES
    }
    latency_shape = _latency_shape(latencies, "event_latency")
    result = {
        "event_count": float(count),
        "event_rate_per_second": round(count / duration_seconds, 6),
        "event_error_rate": _rate(
            sum(item.get("level") in {"ERROR", "CRITICAL"} for item in records), count
        ),
        "event_failure_rate": _rate(sum(item.get("success") is False for item in records), count),
        "event_success_rate": _rate(sum(item.get("success") is True for item in records), count),
        **latency_shape,
        "event_non_2xx_rate": _rate(
            sum(status is not None and not 200 <= int(status) < 300 for status in statuses), count
        ),
        "event_error_type_rate": _rate(sum(bool(value) for value in error_types), count),
        "event_timeout_error_rate": _rate(
            sum("timeout" in value or "timed_out" in value for value in error_types), count
        ),
        "event_connection_error_rate": _rate(
            sum(
                any(token in value for token in ("connect", "network", "socket", "reset"))
                for value in error_types
            ),
            count,
        ),
        "event_dependency_failure_rate": _rate(
            sum(
                item.get("downstream_service") is not None and item.get("success") is False
                for item in records
            ),
            count,
        ),
        "event_downstream_call_rate": _rate(
            sum(item.get("downstream_service") is not None for item in records), count
        ),
        "event_distinct_service_count": float(len(services)),
        "event_distinct_type_count": float(len(types)),
    }
    result.update(
        {f"event_service_{service}_count": float(services[service]) for service in SERVICE_NAMES}
    )
    result.update(
        {
            f"event_service_{service}_failure_rate": _rate(
                sum(item.get("success") is False for item in service_records[service]),
                len(service_records[service]),
            )
            for service in SERVICE_NAMES
        }
    )
    result.update(
        {
            f"event_service_{service}_latency_p95_ms": float(
                _percentile(
                    [
                        value
                        for item in service_records[service]
                        if (value := _numeric(item.get("latency_ms"))) is not None
                    ],
                    0.95,
                )
                or 0.0
            )
            for service in SERVICE_NAMES
        }
    )
    result.update(
        {
            f"event_downstream_{service}_failure_rate": _rate(
                sum(item.get("success") is False for item in downstream_records[service]),
                len(downstream_records[service]),
            )
            for service in SERVICE_NAMES
        }
    )
    return result


def eligible_runtime_event(event: dict[str, Any]) -> bool:
    if str(event.get("service")) not in SERVICE_NAMES:
        return False
    event_type = str(event.get("event_type") or "")
    path = str(event.get("path") or "")
    if event_type == "health_check_completed" or path.startswith("/health/"):
        return False
    if event_type.startswith("intelligence_") or path.startswith(INTELLIGENCE_PATH_PREFIX):
        return False
    return True


def _scenario_from_status(status_code: int | None) -> str:
    return {
        401: "invalid_login",
        404: "invalid_account",
        422: "invalid_amount",
    }.get(status_code, "valid")


def _request_observation(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("service") != "gateway":
        return None
    if event.get("event_type") != "http_request_completed":
        return None
    if event.get("path") != "/api/v1/transactions":
        return None
    latency_ms = float(event.get("latency_ms") or 0.0)
    ended_at = _parse_timestamp(event["timestamp"])
    started_at = ended_at - timedelta(milliseconds=max(0.0, latency_ms))
    status_value = event.get("status_code")
    status_code = int(status_value) if status_value is not None else None
    expected_outcome = status_code in {200, 401, 404, 422}
    return {
        "started_at": _iso(started_at),
        "latency_ms": latency_ms,
        "status_code": status_code,
        "expected_outcome": expected_outcome,
        "transport_error": None,
        "scenario": _scenario_from_status(status_code),
    }


def _fingerprint(event: dict[str, Any]) -> str:
    payload = json.dumps(event, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LiveTelemetryBuffer:
    def __init__(self, max_age_seconds: int = MAX_BUFFER_SECONDS) -> None:
        if max_age_seconds < max(WINDOW_SECONDS):
            raise ValueError("live telemetry buffer must retain at least 60 seconds")
        self.max_age_seconds = max_age_seconds
        self._events: deque[tuple[datetime, str, dict[str, Any]]] = deque()
        self._seen: set[str] = set()
        self._watermark: datetime | None = None
        self._first_observed: datetime | None = None
        self._lock = RLock()

    def ingest(self, events: list[StructuredLogEvent]) -> dict[str, Any]:
        accepted = 0
        ignored = 0
        duplicates = 0
        ordered = sorted(events, key=lambda item: item.timestamp)
        with self._lock:
            for model in ordered:
                event = model.model_dump(mode="json")
                if not eligible_runtime_event(event):
                    ignored += 1
                    continue
                timestamp = _parse_timestamp(event["timestamp"])
                fingerprint = _fingerprint(event)
                if fingerprint in self._seen:
                    duplicates += 1
                    continue
                self._events.append((timestamp, fingerprint, event))
                self._seen.add(fingerprint)
                self._watermark = max(self._watermark or timestamp, timestamp)
                self._first_observed = min(self._first_observed or timestamp, timestamp)
                accepted += 1
            if accepted:
                self._events = deque(sorted(self._events, key=lambda item: item[0]))
            self._prune_locked()
            return {
                "accepted": accepted,
                "ignored": ignored,
                "duplicates": duplicates,
                "buffered_events": len(self._events),
                "watermark": _iso(self._watermark) if self._watermark else None,
            }

    def _prune_locked(self) -> None:
        if self._watermark is None:
            return
        cutoff = self._watermark - timedelta(seconds=self.max_age_seconds)
        changed = False
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
            changed = True
        if changed:
            self._seen = {fingerprint for _, fingerprint, _ in self._events}
        self._first_observed = self._events[0][0] if self._events else None

    def snapshot(self, window_seconds: int) -> dict[str, Any]:
        if window_seconds not in WINDOW_SECONDS:
            raise ValueError("live model window must be 10 or 60 seconds")
        with self._lock:
            if self._watermark is None or self._first_observed is None:
                raise LiveWindowNotReady("no live telemetry has been observed")
            coverage = (self._watermark - self._first_observed).total_seconds()
            if coverage < window_seconds:
                raise LiveWindowNotReady(
                    f"live telemetry coverage is {coverage:.3f}s; "
                    f"{window_seconds}s is required"
                )
            ended_at = self._watermark
            started_at = ended_at - timedelta(seconds=window_seconds)
            # Rolling event-time windows use (start, end] so the newest watermark
            # event is included without double-counting the previous boundary.
            events = [
                event
                for timestamp, _, event in self._events
                if started_at < timestamp <= ended_at
            ]

        requests = []
        for event in events:
            observation = _request_observation(event)
            if observation is None:
                continue
            request_started_at = _parse_timestamp(observation["started_at"])
            if started_at < request_started_at <= ended_at:
                requests.append(observation)
        features = request_features(requests, float(window_seconds))
        features.update(event_features(events, float(window_seconds)))
        if any(not math.isfinite(value) for value in features.values()):
            raise RuntimeError("live feature window contains a non-finite value")
        return {
            "window_seconds": window_seconds,
            "window_started_at": _iso(started_at),
            "window_ended_at": _iso(ended_at),
            "coverage_seconds": round(
                (ended_at - (self._first_observed or ended_at)).total_seconds(), 6
            ),
            "request_count": len(requests),
            "event_count": len(events),
            "features": features,
        }


_live_telemetry_buffer = LiveTelemetryBuffer()


def get_live_telemetry_buffer() -> LiveTelemetryBuffer:
    return _live_telemetry_buffer
