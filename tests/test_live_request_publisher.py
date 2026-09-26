from __future__ import annotations

import asyncio
import json

import httpx

from scripts.live_request_telemetry import (
    LiveRequestTelemetryPublisher,
    request_observation,
)


RESULT = {
    "request_id": "live-000001-request-00001",
    "trace_id": "trace-1",
    "started_at": "2026-09-26T10:00:00Z",
    "latency_ms": 66.089,
    "status_code": 401,
    "expected_outcome": True,
    "transport_error": None,
    "scenario": "invalid_login",
}


def test_request_observation_contains_only_runtime_request_contract() -> None:
    assert request_observation(RESULT) == {
        "request_id": "live-000001-request-00001",
        "trace_id": "trace-1",
        "started_at": "2026-09-26T10:00:00Z",
        "latency_ms": 66.089,
        "status_code": 401,
        "expected_outcome": True,
        "transport_error": None,
        "scenario": "invalid_login",
    }


def test_publisher_batches_completed_request_observations() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        payloads.append(payload)
        count = len(payload["requests"])
        return httpx.Response(
            200,
            json={
                "status": "accepted",
                "accepted_requests": count,
                "duplicate_requests": 0,
            },
            request=request,
        )

    async def scenario() -> LiveRequestTelemetryPublisher:
        publisher = LiveRequestTelemetryPublisher(
            "http://gateway",
            batch_size=2,
            flush_interval=0.05,
            transport=httpx.MockTransport(handler),
        )
        async with publisher:
            await publisher.observe(RESULT)
            second = dict(RESULT)
            second["request_id"] = "live-000001-request-00002"
            await publisher.observe(second)
        return publisher

    publisher = asyncio.run(scenario())

    assert len(payloads) == 1
    assert len(payloads[0]["requests"]) == 2
    assert publisher.accepted_total == 2
    assert publisher.duplicate_total == 0
    assert publisher.batches_total == 1
