from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.intelligence_live import (
    LiveTelemetryBuffer,
    _request_observation,
    event_features,
    request_features,
)
from datasets.final_features import _event_features as offline_event_features
from datasets.final_features import _request_features as offline_request_features
from faultweave_common.logging import StructuredLogEvent


def _event(
    at: datetime,
    *,
    service: str = "gateway",
    event_type: str = "http_request_completed",
    path: str | None = "/api/v1/transactions",
    status_code: int | None = 200,
    latency_ms: float | None = 20.0,
    success: bool | None = True,
    downstream_service: str | None = None,
    error_type: str | None = None,
    level: str = "INFO",
) -> StructuredLogEvent:
    return StructuredLogEvent(
        timestamp=at,
        service=service,
        environment="test",
        level=level,
        event_type=event_type,
        message="test event",
        run_id="live-test",
        request_id=f"request-{at.timestamp()}",
        trace_id="trace-live-test",
        method="POST" if path else None,
        path=path,
        status_code=status_code,
        latency_ms=latency_ms,
        outcome="success" if success else "failure",
        success=success,
        downstream_service=downstream_service,
        error_type=error_type,
    )


def test_live_request_feature_math_matches_frozen_offline_extractor() -> None:
    records = [
        {
            "started_at": "2026-09-26T10:00:00Z",
            "latency_ms": 20.0,
            "status_code": 200,
            "expected_outcome": True,
            "transport_error": None,
            "scenario": "valid",
        },
        {
            "started_at": "2026-09-26T10:00:01Z",
            "latency_ms": 45.0,
            "status_code": 401,
            "expected_outcome": True,
            "transport_error": None,
            "scenario": "invalid_login",
        },
        {
            "started_at": "2026-09-26T10:00:02Z",
            "latency_ms": 80.0,
            "status_code": 503,
            "expected_outcome": False,
            "transport_error": None,
            "scenario": "valid",
        },
    ]

    assert request_features(records, 10.0) == offline_request_features(records, 10.0)


def test_live_event_feature_math_matches_frozen_offline_extractor() -> None:
    records = [
        {
            "service": "gateway",
            "event_type": "http_request_completed",
            "level": "INFO",
            "success": True,
            "status_code": 200,
            "latency_ms": 20.0,
            "downstream_service": None,
            "error_type": None,
        },
        {
            "service": "transaction",
            "event_type": "downstream_request_failed",
            "level": "ERROR",
            "success": False,
            "status_code": 504,
            "latency_ms": 900.0,
            "downstream_service": "payment",
            "error_type": "ReadTimeout",
        },
        {
            "service": "payment",
            "event_type": "http_request_completed",
            "level": "ERROR",
            "success": False,
            "status_code": 500,
            "latency_ms": 400.0,
            "downstream_service": None,
            "error_type": "ConnectionResetError",
        },
    ]

    assert event_features(records, 10.0) == offline_event_features(records, 10.0)


def test_gateway_completion_derives_observable_request_semantics() -> None:
    at = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    observation = _request_observation(_event(at, status_code=503).model_dump(mode="json"))

    assert observation is not None
    assert observation["scenario"] == "valid"
    assert observation["expected_outcome"] is False
    assert observation["transport_error"] is None
    assert observation["latency_ms"] == 20.0


def test_buffer_deduplicates_and_builds_complete_ten_second_window() -> None:
    buffer = LiveTelemetryBuffer()
    started = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    first = _event(started)
    second = _event(started + timedelta(seconds=11), latency_ms=25.0)

    first_summary = buffer.ingest([first, second])
    duplicate_summary = buffer.ingest([second])
    snapshot = buffer.snapshot(10)

    assert first_summary["accepted"] == 2
    assert duplicate_summary["duplicates"] == 1
    assert snapshot["window_seconds"] == 10
    assert snapshot["request_count"] == 1
    assert snapshot["event_count"] == 1
    assert len(snapshot["features"]) == 65
    assert snapshot["features"]["request_count"] == 1.0


def test_buffer_supports_sixty_second_window_without_ground_truth_fields() -> None:
    buffer = LiveTelemetryBuffer()
    started = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    events = [_event(started + timedelta(seconds=index * 10)) for index in range(7)]

    buffer.ingest(events)
    snapshot = buffer.snapshot(60)

    assert snapshot["coverage_seconds"] == 60.0
    assert snapshot["request_count"] == 6
    assert snapshot["event_count"] == 6
    assert "fault_id" not in snapshot
    assert "label" not in snapshot
    assert "interval" not in snapshot
