from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.intelligence_live import LiveTelemetryBuffer
from app.main import app, get_live_telemetry_buffer
from fastapi.testclient import TestClient


def _event(at: datetime, path: str = "/api/v1/transactions") -> dict[str, object]:
    return {
        "schema_version": "1.1",
        "timestamp": at.isoformat(),
        "service": "gateway",
        "environment": "test",
        "level": "INFO",
        "event_type": "http_request_completed",
        "message": "HTTP request completed",
        "run_id": "api-live-test",
        "request_id": f"request-{at.timestamp()}",
        "trace_id": "trace-api-live-test",
        "method": "POST",
        "path": path,
        "status_code": 200,
        "latency_ms": 25.0,
        "outcome": "success",
        "success": True,
        "user_id": None,
        "transaction_id": None,
        "payment_id": None,
        "downstream_service": None,
        "error_type": None,
        "attributes": {},
    }


def test_telemetry_endpoint_ingests_and_exposes_live_features() -> None:
    buffer = LiveTelemetryBuffer()
    app.dependency_overrides[get_live_telemetry_buffer] = lambda: buffer
    started = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/intelligence/telemetry",
                json={"events": [_event(started), _event(started + timedelta(seconds=11))]},
            )
            features = client.get("/api/v1/intelligence/live/features/10")
        assert response.status_code == 200
        assert response.json()["accepted"] == 2
        assert features.status_code == 200
        assert features.json()["window_seconds"] == 10
        assert len(features.json()["features"]) == 65
    finally:
        app.dependency_overrides.clear()


def test_live_feature_endpoint_returns_conflict_during_warmup() -> None:
    buffer = LiveTelemetryBuffer()
    app.dependency_overrides[get_live_telemetry_buffer] = lambda: buffer
    started = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    try:
        with TestClient(app) as client:
            client.post("/api/v1/intelligence/telemetry", json={"events": [_event(started)]})
            response = client.get("/api/v1/intelligence/live/features/10")
        assert response.status_code == 409
        assert "10s is required" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_telemetry_endpoint_ignores_intelligence_feedback_events() -> None:
    buffer = LiveTelemetryBuffer()
    app.dependency_overrides[get_live_telemetry_buffer] = lambda: buffer
    at = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/intelligence/telemetry",
                json={"events": [_event(at, "/api/v1/intelligence/telemetry")]},
            )
        assert response.status_code == 200
        assert response.json()["accepted"] == 0
        assert response.json()["ignored"] == 1
    finally:
        app.dependency_overrides.clear()
