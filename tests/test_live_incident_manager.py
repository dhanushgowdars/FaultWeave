from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.intelligence_incidents import LiveIncidentManager


class FakeBuffer:
    def __init__(self) -> None:
        self.current_end = datetime(2026, 9, 26, 10, 1, 10, tzinfo=UTC)
        self.signal_start = self.current_end - timedelta(seconds=10)

    def snapshot(
        self,
        window_seconds: int,
        *,
        require_client_requests: bool = False,
    ) -> dict[str, Any]:
        assert require_client_requests is True
        return self._snapshot(window_seconds, self.current_end)

    def snapshot_ending_at(
        self,
        window_seconds: int,
        ended_at: datetime,
        *,
        start_tolerance_seconds: float = 0.0,
        require_client_requests: bool = False,
    ) -> dict[str, Any]:
        assert start_tolerance_seconds >= 0
        assert require_client_requests is True
        return self._snapshot(window_seconds, ended_at)

    def _snapshot(self, window_seconds: int, ended_at: datetime) -> dict[str, Any]:
        failure = 0.0 if ended_at <= self.signal_start else 0.8
        features = {
            "request_unexpected_outcome_rate": failure,
            "request_server_error_rate": failure,
            "request_transport_error_rate": 0.0,
            "event_failure_rate": failure,
            "event_dependency_failure_rate": failure,
            "request_latency_p95_ms": 100.0 if failure == 0 else 500.0,
            "event_latency_p95_ms": 80.0 if failure == 0 else 400.0,
            "event_service_authentication_failure_rate": failure,
            "event_service_authentication_latency_p95_ms": (
                40.0 if failure == 0 else 220.0
            ),
        }
        return {
            "window_seconds": window_seconds,
            "window_started_at": (ended_at - timedelta(seconds=window_seconds)).isoformat(),
            "window_ended_at": ended_at.isoformat(),
            "features": features,
            "request_count": 10,
            "event_count": 20,
        }

    def events_between(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        start_tolerance_seconds: float = 0.0,
    ) -> tuple[dict[str, Any], ...]:
        assert start_tolerance_seconds >= 0
        failed = ended_at > self.signal_start
        return (
            {
                "timestamp": (started_at + timedelta(seconds=1)).isoformat(),
                "service": "gateway",
                "downstream_service": "authentication",
                "success": not failed,
                "level": "ERROR" if failed else "INFO",
                "status_code": 500 if failed else 200,
                "latency_ms": 400.0 if failed else 20.0,
                "error_type": "DependencyFailure" if failed else None,
                "event_type": "downstream_request_completed",
                "message": "request completed",
            },
            {
                "timestamp": (started_at + timedelta(seconds=2)).isoformat(),
                "service": "authentication",
                "downstream_service": None,
                "success": not failed,
                "level": "ERROR" if failed else "INFO",
                "status_code": 500 if failed else 200,
                "latency_ms": 300.0 if failed else 15.0,
                "error_type": "AuthenticationFailure" if failed else None,
                "event_type": "authentication_failed" if failed else "authentication_succeeded",
                "message": "authentication observation",
            },
        )


def test_incident_opens_enriches_and_resolves(monkeypatch) -> None:
    mode = {"value": "abnormal"}

    def fake_inference(_artifacts, request):
        if request.window_seconds == 10:
            abnormal = mode["value"] == "abnormal"
            return {
                "window_seconds": 10,
                "status": "ABNORMAL_SIGNAL" if abnormal else "NORMAL",
                "anomaly": {
                    "score": 0.5 if abnormal else 0.0,
                    "threshold": 0.2,
                    "is_anomalous": abnormal,
                },
                "classification": None,
            }
        abnormal = mode["value"] == "abnormal"
        return {
            "window_seconds": 60,
            "status": "ABNORMAL" if abnormal else "NORMAL",
            "anomaly": {
                "score": 0.5 if abnormal else 0.0,
                "threshold": 0.2,
                "is_anomalous": abnormal,
            },
            "classification": (
                {
                    "label": "AUTHENTICATION_FAILURE_BURST",
                    "known_class_candidate": "AUTHENTICATION_FAILURE_BURST",
                    "confidence": 0.95,
                    "class_support_score": 1.0,
                    "class_support_limit": 2.0,
                    "accepted_as_known": True,
                }
                if abnormal
                else {
                    "label": "NORMAL",
                    "known_class_candidate": None,
                    "confidence": None,
                    "class_support_score": None,
                    "class_support_limit": None,
                    "accepted_as_known": None,
                }
            ),
        }

    monkeypatch.setattr(
        "app.intelligence_incidents.run_intelligence_inference",
        fake_inference,
    )
    buffer = FakeBuffer()
    manager = LiveIncidentManager()

    opened = manager.evaluate(buffer, object())

    assert opened["status"] == "INCIDENT_OPENED"
    incident = opened["incident"]
    assert incident["state"] == "ACTIVE"
    assert incident["classification"]["label"] == "AUTHENTICATION_FAILURE_BURST"
    assert incident["severity"] is not None
    assert incident["localization"]["probable_origin"] is not None
    incident_id = incident["incident_id"]

    mode["value"] = "normal"
    for _ in range(2):
        buffer.current_end += timedelta(seconds=10)
        update = manager.evaluate(buffer, object())
        assert update["status"] == "INCIDENT_UPDATED"

    buffer.current_end += timedelta(seconds=10)
    resolved = manager.evaluate(buffer, object())

    assert resolved["status"] == "INCIDENT_RESOLVED"
    assert resolved["incident"]["state"] == "RESOLVED"
    assert resolved["incident"]["incident_id"] == incident_id
    assert manager.current_incident() is None
    assert manager.incident(incident_id)["state"] == "RESOLVED"


def test_same_temporal_bucket_refreshes_main_classification(monkeypatch) -> None:
    classification = {"label": "UNKNOWN ABNORMAL PATTERN"}

    def fake_inference(_artifacts, request):
        if request.window_seconds == 10:
            return {
                "window_seconds": 10,
                "status": "ABNORMAL_SIGNAL",
                "anomaly": {
                    "score": 0.5,
                    "threshold": 0.2,
                    "is_anomalous": True,
                },
                "classification": None,
            }
        label = classification["label"]
        return {
            "window_seconds": 60,
            "status": "ABNORMAL",
            "anomaly": {
                "score": 0.5,
                "threshold": 0.2,
                "is_anomalous": True,
            },
            "classification": {
                "label": label,
                "known_class_candidate": "AUTHENTICATION_FAILURE_BURST",
                "confidence": 0.96,
                "class_support_score": 5.0 if label.startswith("UNKNOWN") else 2.0,
                "class_support_limit": 4.24,
                "accepted_as_known": not label.startswith("UNKNOWN"),
            },
        }

    monkeypatch.setattr(
        "app.intelligence_incidents.run_intelligence_inference",
        fake_inference,
    )
    buffer = FakeBuffer()
    manager = LiveIncidentManager()

    opened = manager.evaluate(buffer, object())
    assert opened["incident"]["classification"]["label"] == "UNKNOWN ABNORMAL PATTERN"

    classification["label"] = "AUTHENTICATION_FAILURE_BURST"
    buffer.current_end += timedelta(milliseconds=500)
    refreshed = manager.evaluate(buffer, object())

    assert refreshed["status"] == "INCIDENT_REFRESHED"
    assert (
        refreshed["incident"]["classification"]["label"]
        == "AUTHENTICATION_FAILURE_BURST"
    )
    assert refreshed["incident"]["temporal"]["samples_last_60s"] == 1
