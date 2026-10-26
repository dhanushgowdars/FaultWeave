from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.demo_workload import (
    DemoTelemetryNotReady,
    DemoWorkloadConfig,
    SustainedDemoWorkload,
)
from app.intelligence_live import (
    LiveRequestObservation,
    LiveTelemetryBuffer,
)
from faultweave_common.logging import StructuredLogEvent


def _event(at: datetime, sequence: int) -> StructuredLogEvent:
    return StructuredLogEvent(
        timestamp=at,
        service="gateway",
        environment="test",
        level="INFO",
        event_type="http_request_completed",
        message="Research transaction completed",
        run_id="demo-phase19b",
        request_id=f"event-{sequence}",
        trace_id=f"trace-{sequence}",
        method="POST",
        path="/api/v1/transactions",
        status_code=200,
        latency_ms=20.0,
        outcome="success",
        success=True,
    )


def _request(at: datetime, sequence: int) -> LiveRequestObservation:
    return LiveRequestObservation(
        request_id=f"request-{sequence}",
        trace_id=f"trace-{sequence}",
        started_at=at,
        latency_ms=25.0,
        status_code=200,
        expected_outcome=True,
        transport_error=None,
        scenario="valid",
    )


def _config(**overrides) -> DemoWorkloadConfig:
    values = {
        "gateway_url": "http://test",
        "baseline_seconds": 15.0,
        "challenge_seconds": 30.0,
        "recovery_seconds": 20.0,
        "requests_per_second": 20.0,
        "max_concurrency": 2,
        "bridge_wait_seconds": 0.05,
    }
    values.update(overrides)
    return DemoWorkloadConfig(**values)


def test_workload_rejects_profile_shorter_than_model_window() -> None:
    with pytest.raises(ValueError, match="60-second window"):
        _config(baseline_seconds=1, challenge_seconds=1, recovery_seconds=1)


def test_workload_uses_real_response_as_client_observation() -> None:
    observed_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_headers.update(request.headers)
        return httpx.Response(200, json={"status": "COMPLETED"})

    buffer = LiveTelemetryBuffer()
    workload = SustainedDemoWorkload(
        buffer,
        _config(),
        transport=httpx.MockTransport(handler),
    )
    summary = asyncio.run(workload.run_phase("demo-phase19b", "baseline", 0.04))

    assert summary["request_count"] == 1
    assert summary["successful_count"] == 1
    assert observed_headers["x-run-id"] == "demo-phase19b"
    assert observed_headers["x-request-id"].startswith("demo-phase19b-model-baseline-")
    assert buffer._explicit_request_mode is True


def test_model_window_requires_events_and_client_observations() -> None:
    buffer = LiveTelemetryBuffer()
    started = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)
    events = [_event(started + timedelta(seconds=index * 10), index) for index in range(7)]
    requests = [_request(started + timedelta(seconds=index * 10), index) for index in range(7)]
    buffer.ingest(events, requests)
    workload = SustainedDemoWorkload(buffer, _config())

    snapshot = asyncio.run(workload.require_model_window())

    assert snapshot["window_seconds"] == 60
    assert snapshot["request_observation_source"] == "client_observed"
    assert snapshot["event_count"] == 6
    assert "features" not in snapshot


def test_bridge_preflight_fails_closed_without_service_events() -> None:
    buffer = LiveTelemetryBuffer()
    workload = SustainedDemoWorkload(buffer, _config())

    with pytest.raises(DemoTelemetryNotReady, match="live_telemetry_bridge.py"):
        asyncio.run(workload.require_recent_events(datetime.now(UTC)))
