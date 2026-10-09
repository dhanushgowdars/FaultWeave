from __future__ import annotations

from app import transfer_api
from app.intelligence_live import LiveTelemetryBuffer
from app.main import app
from fastapi.testclient import TestClient
from faultweave_common.security import create_access_token

TRANSFER_EVIDENCE = {
    "id": "transfer-api-test",
    "state": "REQUIRES_REVIEW",
    "funds_status": "UNKNOWN",
    "timeline": [
        {
            "event_id": "failed-event",
            "timestamp": "2026-10-09T03:00:00+00:00",
            "service": "transaction",
            "step": "payment_execution",
            "status": "FAILED",
            "detail": "Payment response unavailable",
        }
    ],
    "case_history": {
        "failures": [
            {
                "service": "payment",
                "step": "payment_execution",
                "error": {"error_type": "ConnectError", "detail": "Connection refused"},
            }
        ],
        "reviews": [],
        "recoveries": [],
    },
    "investigation": {
        "observed_failure_service": "payment",
        "evidence_event_ids": ["failed-event"],
        "next_action": "Restore payment and submit an engineer review.",
    },
}


def test_authenticated_transfer_intelligence_endpoint_is_evidence_first(monkeypatch) -> None:
    async def proxy(*_args, **_kwargs):
        return TRANSFER_EVIDENCE

    monkeypatch.setattr(transfer_api, "_proxy", proxy)
    monkeypatch.setattr(transfer_api, "get_live_telemetry_buffer", LiveTelemetryBuffer)
    # No telemetry exists, so artifact internals must never be consulted.
    monkeypatch.setattr(transfer_api, "get_frozen_artifacts", object)
    token = create_access_token("customer-test", role="customer")
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/transfers/transfer-api-test/intelligence",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transfer_id"] == "transfer-api-test"
    assert body["probable_origin"]["service"] == "payment"
    assert body["model"]["status"] == "WINDOW_NOT_READY"
    assert body["model"]["result"] is None


def test_transfer_intelligence_endpoint_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/transfers/transfer-api-test/intelligence")

    # FastAPI rejects the missing required Authorization header before the
    # endpoint can load artifacts or inspect transfer evidence.
    assert response.status_code == 422
