from __future__ import annotations

from app.main import (
    app,
    get_intelligence_artifacts,
    get_live_incident_manager,
    get_live_intelligence_stream,
    get_live_telemetry_buffer,
)
from fastapi.testclient import TestClient


INCIDENT = {
    "incident_id": "inc-test-0001",
    "state": "ACTIVE",
    "classification": {"label": "AUTHENTICATION_FAILURE_BURST"},
    "localization": {"probable_origin": "authentication", "top_3": []},
    "severity": {"level": "HIGH", "score": 0.7},
}


class StubManager:
    def evaluate(self, _buffer, _artifacts):
        return {"status": "INCIDENT_OPENED", "incident": INCIDENT}

    def current_incident(self):
        return INCIDENT

    def incidents(self):
        return [INCIDENT]

    def incident(self, incident_id: str):
        return INCIDENT if incident_id == INCIDENT["incident_id"] else None


class StubStream:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event: str, data: dict):
        self.events.append((event, data))
        return None


def test_incident_lifecycle_endpoints_expose_active_incident() -> None:
    manager = StubManager()
    stream = StubStream()
    app.dependency_overrides[get_live_incident_manager] = lambda: manager
    app.dependency_overrides[get_live_telemetry_buffer] = lambda: object()
    app.dependency_overrides[get_intelligence_artifacts] = lambda: object()
    app.dependency_overrides[get_live_intelligence_stream] = lambda: stream
    try:
        with TestClient(app) as client:
            evaluated = client.post("/api/v1/intelligence/live/evaluate")
            current = client.get("/api/v1/intelligence/incidents/current")
            listing = client.get("/api/v1/intelligence/incidents")
            detail = client.get("/api/v1/intelligence/incidents/inc-test-0001")
        assert evaluated.status_code == 200
        assert evaluated.json()["status"] == "INCIDENT_OPENED"
        assert current.json() == {"active": True, "incident": INCIDENT}
        assert listing.json() == {"count": 1, "incidents": [INCIDENT]}
        assert detail.json() == INCIDENT
        assert len(stream.events) == 1
        assert stream.events[0][0] == "incident.opened"
    finally:
        app.dependency_overrides.clear()


def test_unknown_incident_returns_not_found() -> None:
    app.dependency_overrides[get_live_incident_manager] = lambda: StubManager()
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/intelligence/incidents/missing")
        assert response.status_code == 404
        assert response.json()["detail"] == "incident not found"
    finally:
        app.dependency_overrides.clear()
