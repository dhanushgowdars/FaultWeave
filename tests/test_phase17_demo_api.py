from __future__ import annotations

from copy import deepcopy

from app import demo_api
from app.demo_orchestration import DemoFaultControl, DemoSessionStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from faultweave_common.security import authenticated_identity


class FakeAssistant:
    async def explain(self, evidence, intelligence, payload):
        assert evidence["transfer_id"] == "transfer-1"
        assert payload.focus == "overview"
        return {
            "schema_version": "1.0",
            "transfer_id": "transfer-1",
            "focus": "overview",
            "status": "DETERMINISTIC_FALLBACK",
            "analysis": {"headline": "Grounded test explanation"},
            "evidence_index": {"event-failed": {"step": "ledger_posting"}},
            "provenance": {"ground_truth_used": False},
            "safety": {"read_only": True},
        }


def evidence(*, recovered: bool = False):
    return {
        "schema_version": "1.0",
        "transfer_id": "transfer-1",
        "state": "COMPLETED" if recovered else "REQUIRES_REVIEW",
        "created_at": "2026-10-10T00:00:00+00:00",
        "transfer": {
            "source_account_number": "FW-DEMO-001",
            "destination_account_number": "FW-DEST",
            "amount_minor": 1000,
            "currency": "INR",
            "idempotency_key": "demo-key-123",
        },
        "funds_status": "COMMITTED",
        "requires_review": not recovered,
        "timeline": [
            {
                "event_id": "event-failed",
                "sequence": 1,
                "attempt": 1,
                "timestamp": "2026-10-10T00:00:01+00:00",
                "service": "payment",
                "step": "ledger_posting",
                "status": "FAILED",
                "detail": "Ledger unavailable",
                "request_id": "request-1",
                "trace_id": "trace-1",
                "run_id": "demo-run",
            }
        ],
        "case_history": {
            "failures": [{"step": "ledger_posting", "event_id": "event-failed"}],
            "reviews": [{"event_id": "review-1"}] if recovered else [],
            "recoveries": [{"event_id": "recovery-1"}] if recovered else [],
        },
        "investigation": {"requires_review": not recovered},
        "journal": {
            "entries": [
                {"direction": "DEBIT", "amount_minor": 1000},
                {"direction": "CREDIT", "amount_minor": 1000},
            ]
        }
        if recovered
        else None,
        "evidence_sources": {"workflow": "db", "payment": "db", "journal": "db"},
    }


def test_fault_demo_requires_analysis_and_approval_before_recovery(tmp_path, monkeypatch) -> None:
    state = {"recovered": False}
    store = DemoSessionStore(tmp_path / "sessions")
    control = DemoFaultControl(tmp_path / "fault" / "active_fault.json")

    async def fake_service(target, path, authorization, payload=None, method="POST"):
        if path == "/internal/v1/auth/login":
            return {"access_token": "customer-token"}
        if path == "/internal/v1/accounts/demo/create":
            return {"account_number": payload["account_number"], "balance_minor": 0}
        if path == "/internal/v1/accounts/lookup":
            source = payload["account_number"] == "FW-DEMO-001"
            balance = 99_000 if state["recovered"] and source else 100_000 if source else 1000
            if not state["recovered"] and not source:
                balance = 0
            return {"account_number": payload["account_number"], "balance_minor": balance}
        if path == "/internal/v1/transfers":
            return {"id": "transfer-1", "state": "REQUIRES_REVIEW"}
        if path.endswith("/resume"):
            state["recovered"] = True
            return {"id": "transfer-1", "state": "COMPLETED"}
        if path.endswith("/evidence"):
            return deepcopy(evidence(recovered=state["recovered"]))
        if path == "/internal/v1/transfers/transfer-1":
            return {"id": "transfer-1", "state": "REQUIRES_REVIEW", "timeline": []}
        raise AssertionError((target, path, method))

    monkeypatch.setattr(demo_api, "_service", fake_service)
    monkeypatch.setattr(
        demo_api,
        "build_transfer_intelligence",
        lambda *_: {"model": {"status": "WINDOW_NOT_READY"}, "ground_truth_used": False},
    )

    app = FastAPI()
    app.include_router(demo_api.router)
    app.dependency_overrides[authenticated_identity] = lambda: {
        "subject": "engineer-id",
        "role": "engineer",
    }
    app.dependency_overrides[demo_api.get_demo_store] = lambda: store
    app.dependency_overrides[demo_api.get_demo_fault_control] = lambda: control
    app.dependency_overrides[demo_api.get_incident_assistant] = lambda: FakeAssistant()
    client = TestClient(app)
    headers = {"Authorization": "Bearer engineer-token"}

    created = client.post(
        "/api/v1/demo/sessions",
        json={
            "scenario_id": "ledger-service-unavailable",
            "destination_account_number": "FW-DEST",
        },
        headers=headers,
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    armed = client.post(f"/api/v1/demo/sessions/{session_id}/arm", headers=headers)
    assert armed.status_code == 200
    executed = client.post(
        f"/api/v1/demo/sessions/{session_id}/execute", headers=headers
    )
    assert executed.status_code == 200
    assert executed.json()["session"]["state"] == "AWAITING_REVIEW"
    assert control.read() is None

    blocked = client.post(
        f"/api/v1/demo/sessions/{session_id}/approve",
        json={"note": "Reviewed the evidence and approved safe resume."},
        headers=headers,
    )
    assert blocked.status_code == 409
    analyzed = client.post(
        f"/api/v1/demo/sessions/{session_id}/analyze", headers=headers
    )
    assert analyzed.status_code == 200
    assert analyzed.json()["assistant"]["provenance"]["ground_truth_used"] is False
    approved = client.post(
        f"/api/v1/demo/sessions/{session_id}/approve",
        json={"note": "Reviewed the evidence and approved safe resume."},
        headers=headers,
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "APPROVED"
    recovered = client.post(
        f"/api/v1/demo/sessions/{session_id}/recover", headers=headers
    )
    assert recovered.status_code == 200
    final = recovered.json()["session"]
    assert final["state"] == "RECOVERED"
    assert final["verification"]["single_funds_movement"] is True
    assert [event["sequence"] for event in final["events"]] == list(
        range(1, len(final["events"]) + 1)
    )


def test_customer_cannot_use_demo_control_api() -> None:
    app = FastAPI()
    app.include_router(demo_api.router)
    app.dependency_overrides[authenticated_identity] = lambda: {
        "subject": "customer-id",
        "role": "customer",
    }
    response = TestClient(app).get(
        "/api/v1/demo/scenarios", headers={"Authorization": "Bearer customer"}
    )
    assert response.status_code == 403
