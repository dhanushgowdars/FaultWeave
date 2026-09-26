from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.intelligence_live import (
    LiveRequestObservation,
    LiveTelemetryBuffer,
    LiveWindowNotReady,
)
from faultweave_common.logging import StructuredLogEvent


def _event(at: datetime, latency_ms: float = 20.0) -> StructuredLogEvent:
    return StructuredLogEvent(
        timestamp=at,
        service="gateway",
        environment="test",
        level="INFO",
        event_type="http_request_completed",
        message="HTTP request completed",
        run_id="request-source-test",
        request_id=f"event-{at.timestamp()}",
        trace_id="trace-event",
        method="POST",
        path="/api/v1/transactions",
        status_code=200,
        latency_ms=latency_ms,
        outcome="success",
        success=True,
    )


def _request(
    at: datetime,
    *,
    latency_ms: float,
    sequence: int,
) -> LiveRequestObservation:
    return LiveRequestObservation(
        request_id=f"request-{sequence}",
        trace_id=f"trace-{sequence}",
        started_at=at,
        latency_ms=latency_ms,
        status_code=200,
        expected_outcome=True,
        transport_error=None,
        scenario="valid",
    )


def test_frozen_inference_requires_client_observed_request_telemetry() -> None:
    buffer = LiveTelemetryBuffer()
    start = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    buffer.ingest([_event(start), _event(start + timedelta(seconds=11))])

    with pytest.raises(LiveWindowNotReady, match="client-observed request telemetry"):
        buffer.snapshot(10, require_client_requests=True)


def test_explicit_client_latency_replaces_gateway_latency_for_model_features() -> None:
    buffer = LiveTelemetryBuffer()
    start = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    events = [
        _event(start, latency_ms=20.0),
        _event(start + timedelta(seconds=11), latency_ms=20.0),
    ]
    requests = [
        _request(start + timedelta(seconds=1), latency_ms=66.0, sequence=1),
        _request(
            start + timedelta(seconds=10, milliseconds=500),
            latency_ms=70.0,
            sequence=2,
        ),
    ]

    summary = buffer.ingest(events, requests)
    snapshot = buffer.snapshot(10, require_client_requests=True)

    assert summary["accepted_requests"] == 2
    assert snapshot["request_observation_source"] == "client_observed"
    assert snapshot["request_count"] == 1
    assert snapshot["features"]["request_latency_p50_ms"] == 70.0


def test_feature_only_snapshot_marks_gateway_derived_request_semantics() -> None:
    buffer = LiveTelemetryBuffer()
    start = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    buffer.ingest(
        [_event(start), _event(start + timedelta(seconds=11), latency_ms=25.0)]
    )

    snapshot = buffer.snapshot(10)

    assert snapshot["request_observation_source"] == "gateway_derived"
    assert snapshot["features"]["request_latency_p50_ms"] == 25.0


def test_request_observations_are_deduplicated() -> None:
    buffer = LiveTelemetryBuffer()
    start = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    events = [_event(start), _event(start + timedelta(seconds=11))]
    observation = _request(start + timedelta(seconds=10), latency_ms=67.0, sequence=1)

    first = buffer.ingest(events, [observation])
    second = buffer.ingest([], [observation])

    assert first["accepted_requests"] == 1
    assert second["accepted_requests"] == 0
    assert second["duplicate_requests"] == 1
