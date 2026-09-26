from __future__ import annotations

from fastapi.testclient import TestClient

from app.intelligence_artifacts import ArtifactLoadError
from app.main import app, get_intelligence_artifacts


class ReadyArtifacts:
    def readiness(self) -> dict[str, object]:
        return {
            "status": "ready",
            "main_window_seconds": 60,
            "temporal_window_seconds": 10,
            "feature_count": 65,
            "known_class_count": 9,
            "open_set_label": "UNKNOWN ABNORMAL PATTERN",
        }


def test_intelligence_readiness_returns_frozen_artifact_summary() -> None:
    app.dependency_overrides[get_intelligence_artifacts] = lambda: ReadyArtifacts()
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/intelligence/ready")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "main_window_seconds": 60,
            "temporal_window_seconds": 10,
            "feature_count": 65,
            "known_class_count": 9,
            "open_set_label": "UNKNOWN ABNORMAL PATTERN",
        }
    finally:
        app.dependency_overrides.clear()


def test_intelligence_readiness_fails_closed_when_artifacts_are_unavailable() -> None:
    def unavailable() -> ReadyArtifacts:
        raise ArtifactLoadError("model artifact missing")

    app.dependency_overrides[get_intelligence_artifacts] = unavailable
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/intelligence/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "reason": "frozen intelligence artifacts unavailable",
        }
    finally:
        app.dependency_overrides.clear()
