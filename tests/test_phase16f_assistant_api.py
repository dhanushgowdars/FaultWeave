from __future__ import annotations

from app import transfer_api
from app.incident_assistant import AssistantSettings, GroundedIncidentAssistant
from app.main import app
from fastapi.testclient import TestClient
from faultweave_common.security import create_access_token

from tests.test_incident_assistant import evidence


def authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token('phase16f-customer')}"}


async def fake_proxy(target, path, request, payload=None, method="POST"):
    assert target == "transaction"
    assert request.headers["authorization"].startswith("Bearer ")
    if path.endswith("/evidence"):
        return evidence()
    return {
        "id": "transfer-phase16f",
        "state": "REQUIRES_REVIEW",
        "timeline": evidence()["timeline"],
        "case_history": evidence()["case_history"],
        "investigation": evidence()["investigation"],
        "funds_status": "NOT_ATTEMPTED",
    }


def test_assistant_endpoint_is_authenticated_read_only_and_available_without_key(monkeypatch):
    monkeypatch.setattr(transfer_api, "_proxy", fake_proxy)
    assistant = GroundedIncidentAssistant(
        AssistantSettings(
            enabled=False,
            provider="groq",
            model="openai/gpt-oss-120b",
            api_key=None,
            timeout_seconds=1,
            max_attempts=3,
            retry_base_seconds=0.01,
            cache_ttl_seconds=300,
            cache_max_entries=8,
        )
    )
    app.dependency_overrides[transfer_api.get_incident_assistant] = lambda: assistant
    try:
        with TestClient(app) as client:
            denied = client.post(
                "/api/v1/transfers/transfer-phase16f/assistant/explain",
                json={"focus": "overview"},
            )
            response = client.post(
                "/api/v1/transfers/transfer-phase16f/assistant/explain",
                headers=authorization(),
                json={"focus": "overview"},
            )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 422
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "DETERMINISTIC_FALLBACK"
    assert payload["safety"]["read_only"] is True
    assert payload["safety"]["actions_performed"] == []
    assert payload["transfer_id"] == "transfer-phase16f"


def test_assistant_request_rejects_unbounded_or_unknown_input(monkeypatch):
    monkeypatch.setattr(transfer_api, "_proxy", fake_proxy)
    with TestClient(app) as client:
        unknown = client.post(
            "/api/v1/transfers/transfer-phase16f/assistant/explain",
            headers=authorization(),
            json={"focus": "overview", "perform_recovery": True},
        )
        long_question = client.post(
            "/api/v1/transfers/transfer-phase16f/assistant/explain",
            headers=authorization(),
            json={"question": "x" * 401},
        )

    assert unknown.status_code == 422
    assert long_question.status_code == 422
