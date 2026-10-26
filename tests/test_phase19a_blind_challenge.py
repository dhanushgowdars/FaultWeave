from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from app import demo_api, demo_orchestration
from app.demo_orchestration import (
    BLIND_SCENARIO_IDS,
    DemoFaultControl,
    DemoSessionCreate,
    DemoSessionStore,
    _blind_scenario_id,
    new_session,
    public_session,
    verify_blind_commitment,
)
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from faultweave_common.security import authenticated_identity
from pydantic import ValidationError


def _nonce_for(scenario_id: str) -> str:
    for number in range(10_000):
        candidate = f"phase19a-test-{number}"
        if _blind_scenario_id(candidate) == scenario_id:
            return candidate
    raise AssertionError(f"unable to find deterministic nonce for {scenario_id}")


def _engineer_app(store: DemoSessionStore, control: DemoFaultControl) -> FastAPI:
    app = FastAPI()
    app.include_router(demo_api.router)
    app.dependency_overrides[authenticated_identity] = lambda: {
        "subject": "phase19a-engineer",
        "role": "engineer",
    }
    app.dependency_overrides[demo_api.get_demo_store] = lambda: store
    app.dependency_overrides[demo_api.get_demo_fault_control] = lambda: control
    app.dependency_overrides[demo_api.get_demo_workload_runner] = _InstantWorkload
    return app


class _InstantWorkload:
    config = SimpleNamespace(
        baseline_seconds=15.0,
        challenge_seconds=30.0,
        recovery_seconds=20.0,
    )
    profile = {
        "baseline_seconds": 15.0,
        "challenge_seconds": 30.0,
        "recovery_seconds": 20.0,
        "total_seconds": 65.0,
        "requests_per_second": 3.0,
        "traffic_path": "test",
        "financial_scope": "isolated_simulated_transactions",
    }

    async def run_phase(self, _session_id, phase, duration_seconds):
        return {
            "phase": phase,
            "duration_seconds": duration_seconds,
            "request_count": 3,
            "successful_count": 3,
            "affected_count": 0,
            "success_rate": 1.0,
            "server_error_count": 0,
            "transport_error_count": 0,
        }

    async def require_recent_events(self, _phase_started_at):
        return {"window_seconds": 10, "event_count": 3, "request_count": 3}

    async def require_model_window(self):
        return {"window_seconds": 60, "event_count": 18, "request_count": 18}


def test_blind_contract_rejects_client_selected_scenario() -> None:
    with pytest.raises(ValidationError, match="selects the scenario on the server"):
        DemoSessionCreate(mode="BLIND_RANDOM", scenario_id="normal-transfer")
    with pytest.raises(ValidationError, match="requires scenario_id"):
        DemoSessionCreate()


def test_blind_selector_is_deterministic_and_reaches_the_accepted_pool() -> None:
    observed = {_blind_scenario_id(f"coverage-{index}") for index in range(500)}
    assert observed == set(BLIND_SCENARIO_IDS)
    assert _blind_scenario_id("repeatable") == _blind_scenario_id("repeatable")


def test_blind_session_hides_ground_truth_until_reveal(monkeypatch) -> None:
    nonce = _nonce_for("ledger-service-unavailable")
    monkeypatch.setattr(demo_orchestration.secrets, "token_hex", lambda _: nonce)
    session = new_session(DemoSessionCreate(mode="BLIND_RANDOM"), "private-engineer")

    assert session["scenario"]["scenario_id"] == "ledger-service-unavailable"
    assert verify_blind_commitment(session["challenge"], "ledger-service-unavailable")
    public = public_session(session)
    assert public["scenario"]["scenario_id"] == "SEALED_RANDOM_CHALLENGE"
    assert "secret_nonce" not in public["challenge"]
    assert "ledger" not in str(public).lower()


def test_blind_fault_runs_with_internal_arm_and_cleanup(tmp_path, monkeypatch) -> None:
    nonce = _nonce_for("ledger-service-unavailable")
    monkeypatch.setattr(demo_orchestration.secrets, "token_hex", lambda _: nonce)
    store = DemoSessionStore(tmp_path / "demo")
    control = DemoFaultControl(tmp_path / "fault" / "active_fault.json")

    evidence = {
        "schema_version": "1.0",
        "transfer_id": "transfer-blind-1",
        "state": "REQUIRES_REVIEW",
        "funds_status": "COMMITTED",
        "case_history": {"failures": [{"step": "ledger_posting"}]},
        "timeline": [],
    }

    async def fake_service(target, path, authorization, payload=None, method="POST"):
        if path == "/internal/v1/auth/login":
            return {"access_token": "customer-token"}
        if path == "/internal/v1/accounts/demo/create":
            return {"account_number": payload["account_number"], "balance_minor": 0}
        if path == "/internal/v1/accounts/lookup":
            return {"account_number": payload["account_number"], "balance_minor": 100_000}
        if path == "/internal/v1/transfers":
            active = control.read()
            assert active is not None
            assert active["fault_id"] == "SERVICE_UNAVAILABLE"
            assert active["target"] == "ledger"
            return {"id": "transfer-blind-1", "state": "REQUIRES_REVIEW"}
        if path.endswith("/evidence"):
            return deepcopy(evidence)
        raise AssertionError((target, path, method))

    monkeypatch.setattr(demo_api, "_service", fake_service)
    client = TestClient(_engineer_app(store, control))
    headers = {"Authorization": "Bearer engineer-token"}
    created = client.post(
        "/api/v1/demo/sessions",
        json={"mode": "BLIND_RANDOM", "amount_minor": 2500},
        headers=headers,
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert created.json()["scenario"]["scenario_id"] == "SEALED_RANDOM_CHALLENGE"

    direct_arm = client.post(f"/api/v1/demo/sessions/{session_id}/arm", headers=headers)
    assert direct_arm.status_code == 409
    direct_execute = client.post(f"/api/v1/demo/sessions/{session_id}/execute", headers=headers)
    assert direct_execute.status_code == 409

    executed = client.post(f"/api/v1/demo/sessions/{session_id}/run", headers=headers)
    assert executed.status_code == 200
    assert executed.json()["session"]["state"] == "AWAITING_REVIEW"
    assert executed.json()["session"]["scenario"]["scenario_id"] == "SEALED_RANDOM_CHALLENGE"
    assert "activation" not in executed.json()["session"]
    assert control.read() is None
    assert store.load(session_id)["scenario"]["scenario_id"] == "ledger-service-unavailable"

    blocked_reveal = client.post(f"/api/v1/demo/sessions/{session_id}/reveal", headers=headers)
    assert blocked_reveal.status_code == 409


def test_blind_execution_error_always_clears_fault(tmp_path, monkeypatch) -> None:
    nonce = _nonce_for("payment-service-unavailable")
    monkeypatch.setattr(demo_orchestration.secrets, "token_hex", lambda _: nonce)
    store = DemoSessionStore(tmp_path / "demo")
    control = DemoFaultControl(tmp_path / "fault" / "active_fault.json")

    async def unavailable_service(target, path, authorization, payload=None, method="POST"):
        if path == "/internal/v1/auth/login":
            raise HTTPException(status_code=503, detail="authentication unavailable")
        raise AssertionError((target, path, method))

    monkeypatch.setattr(demo_api, "_service", unavailable_service)
    client = TestClient(_engineer_app(store, control))
    headers = {"Authorization": "Bearer engineer-token"}
    created = client.post("/api/v1/demo/sessions", json={"mode": "BLIND_RANDOM"}, headers=headers)
    session_id = created.json()["session_id"]

    failed = client.post(f"/api/v1/demo/sessions/{session_id}/run", headers=headers)
    assert failed.status_code == 503
    assert control.read() is None
    stored = store.load(session_id)
    assert stored["state"] == "EXECUTION_ERROR"
    assert stored["activation"]["cleared_at"] is not None
    assert stored["events"][-1]["action"] == "transfer_execution_error"


def test_reveal_verifies_commitment_after_analysis(tmp_path, monkeypatch) -> None:
    nonce = _nonce_for("normal-transfer")
    monkeypatch.setattr(demo_orchestration.secrets, "token_hex", lambda _: nonce)
    store = DemoSessionStore(tmp_path / "demo")
    control = DemoFaultControl(tmp_path / "fault" / "active_fault.json")
    session = new_session(DemoSessionCreate(mode="BLIND_RANDOM"), "private-engineer")
    session["state"] = "COMPLETED"
    session["analysis_status"] = "READY"
    session["snapshots"]["intelligence"] = {
        "operational_facts": {"failure_observed": False},
        "probable_origin": None,
        "model": {
            "status": "EVALUATED",
            "result": {"classification": {"label": "NORMAL"}},
        },
    }
    store.save(session)
    client = TestClient(_engineer_app(store, control))
    headers = {"Authorization": "Bearer engineer-token"}

    revealed = client.post(f"/api/v1/demo/sessions/{session['session_id']}/reveal", headers=headers)
    assert revealed.status_code == 200
    body = revealed.json()
    assert body["session"]["scenario"]["scenario_id"] == "normal-transfer"
    assert body["session"]["challenge"]["revealed_nonce"] == nonce
    assert body["comparison"]["commitment_verified"] is True
    assert body["comparison"]["model_comparison"]["match"] is True
    assert body["comparison"]["workflow_comparison"]["step_match"] is True
    assert body["comparison"]["model_comparison"]["ground_truth_used_by_model"] is False

    repeated = client.post(f"/api/v1/demo/sessions/{session['session_id']}/reveal", headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["comparison"] == body["comparison"]


def test_blind_analysis_fails_closed_without_evaluated_model(tmp_path, monkeypatch) -> None:
    nonce = _nonce_for("normal-transfer")
    monkeypatch.setattr(demo_orchestration.secrets, "token_hex", lambda _: nonce)
    store = DemoSessionStore(tmp_path / "demo")
    control = DemoFaultControl(tmp_path / "fault" / "active_fault.json")
    session = new_session(DemoSessionCreate(mode="BLIND_RANDOM"), "private-engineer")
    session["state"] = "COMPLETED"
    session["transfer_id"] = "transfer-model-not-ready"
    session["analysis_status"] = "WINDOW_READY"
    store.save(session)

    async def evidence(*_args, **_kwargs):
        return {"id": "transfer-model-not-ready", "timeline": [], "case_history": {}}

    monkeypatch.setattr(demo_api, "_normalized_evidence", evidence)
    monkeypatch.setattr(demo_api, "_raw_evidence", evidence)
    monkeypatch.setattr(
        demo_api,
        "build_transfer_intelligence",
        lambda *_: {
            "decision_authority": {"ground_truth_used": False},
            "model": {"status": "WINDOW_NOT_READY", "result": None},
        },
    )
    monkeypatch.setattr(demo_api, "get_live_telemetry_buffer", lambda: object())
    monkeypatch.setattr(demo_api, "get_frozen_artifacts", lambda: object())
    client = TestClient(_engineer_app(store, control))

    response = client.post(
        f"/api/v1/demo/sessions/{session['session_id']}/analyze",
        headers={"Authorization": "Bearer engineer-token"},
    )

    assert response.status_code == 409
    assert "requires an evaluated frozen-model window" in response.json()["detail"]
    assert store.load(session["session_id"])["analysis_status"] == "WINDOW_READY"
