from __future__ import annotations

from types import SimpleNamespace

from scripts import live_demo_cases
from scripts.live_demo_cases import SCENARIOS, DemoCaseStore


def test_scenarios_explain_controlled_cause_and_recovery() -> None:
    assert len(SCENARIOS) == 9
    for scenario in SCENARIOS.values():
        assert scenario["cause_component"]
        assert scenario["cause_action"]
        assert scenario["recovery_action"]

    database_case = SCENARIOS["DATABASE_UNAVAILABLE"]
    assert database_case["target"] == "postgresql"
    assert database_case["failure_stage"] == "authentication"
    assert "PostgreSQL" in database_case["cause_component"]


def test_demo_case_store_persists_case_identity_and_timeline(tmp_path) -> None:
    path = tmp_path / "cases.sqlite3"
    store = DemoCaseStore(path)

    case = store.create("SERVICE_UNAVAILABLE")
    store.append(
        case,
        "FAULT_ARMED",
        "Payment service stopped",
        "The controlled payment service fault was activated.",
        service="payment",
    )

    reloaded = DemoCaseStore(path).get(case["case_id"])
    assert reloaded is not None
    assert reloaded["request_id"].startswith(reloaded["run_id"])
    assert reloaded["trace_id"]
    assert reloaded["scenario"]["failure_stage"] == "payment"
    assert reloaded["timeline"][0]["phase"] == "FAULT_ARMED"


def test_demo_case_store_lists_newest_first(tmp_path) -> None:
    store = DemoCaseStore(tmp_path / "cases.sqlite3")
    first = store.create("DATABASE_HIGH_LATENCY")
    second = store.create("DOWNSTREAM_TIMEOUT")

    cases = store.list()
    assert {item["case_id"] for item in cases} == {first["case_id"], second["case_id"]}


def test_case_store_hydrates_current_causal_explanation(tmp_path) -> None:
    store = DemoCaseStore(tmp_path / "cases.sqlite3")
    case = store.create("DATABASE_UNAVAILABLE")
    case["scenario"] = {"title": "old presentation"}
    store.save(case)

    reloaded = store.get(case["case_id"])

    assert reloaded is not None
    assert reloaded["scenario"]["failure_stage"] == "authentication"
    assert reloaded["scenario"]["cause_component"] == "Shared PostgreSQL"


def test_fault_injection_does_not_send_a_transaction(tmp_path, monkeypatch) -> None:
    store = DemoCaseStore(tmp_path / "cases.sqlite3")
    case = store.create("SERVICE_UNAVAILABLE")
    activation = SimpleNamespace(
        activation_id="a" * 32,
        fault_id=SimpleNamespace(value="SERVICE_UNAVAILABLE"),
        target="payment",
    )
    control = SimpleNamespace(acquire=lambda **_kwargs: activation, release=lambda _id: None)
    monkeypatch.setattr(live_demo_cases, "FaultStateStore", lambda _path: control)
    monkeypatch.setattr(live_demo_cases, "activate_infrastructure_fault", lambda _item: None)
    monkeypatch.setattr(
        live_demo_cases.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(raise_for_status=lambda: None),
    )

    live_demo_cases.inject_case(store, case)

    assert case["status"] == "FAULT_ACTIVE"
    assert case["transaction_result"] is None
    assert case["recovery_result"] is None


def test_original_transaction_stops_for_engineer_review(tmp_path, monkeypatch) -> None:
    store = DemoCaseStore(tmp_path / "cases.sqlite3")
    case = store.create("SERVICE_UNAVAILABLE")
    case["status"] = "FAULT_ACTIVE"
    case["started_at"] = live_demo_cases.utc_iso()
    store.save(case)
    monkeypatch.setattr(
        live_demo_cases,
        "_transaction",
        lambda run_id, request_id, trace_id, **_kwargs: {
            "request_id": request_id,
            "trace_id": trace_id,
            "status_code": 503,
            "latency_ms": 25.0,
            "started_at": live_demo_cases.utc_iso(),
            "ended_at": live_demo_cases.utc_iso(),
            "succeeded": False,
            "body": {"detail": "payment unavailable"},
        },
    )
    monkeypatch.setattr(live_demo_cases, "_case_evidence", lambda *_args: [])
    monkeypatch.setattr(live_demo_cases, "_model_analysis", lambda *_args: None)

    live_demo_cases.run_case_transaction(store, case)

    assert case["status"] == "AWAITING_REVIEW"
    assert case["review"]["status"] == "PENDING"
    assert case["recovery_result"] is None
    assert case["recovery_verified"] is False
    assert len(case["supporting_requests"]) == 7
    assert all(item["request_id"] != case["request_id"] for item in case["supporting_requests"])


def test_remediation_and_verification_are_separate_actions(tmp_path, monkeypatch) -> None:
    store = DemoCaseStore(tmp_path / "cases.sqlite3")
    case = store.create("SERVICE_UNAVAILABLE")
    activation = SimpleNamespace(
        activation_id="b" * 32,
        fault_id=SimpleNamespace(value="SERVICE_UNAVAILABLE"),
        target="payment",
    )
    case["activation_id"] = activation.activation_id
    case["status"] = "AWAITING_REVIEW"
    case["review"]["status"] = "PENDING"
    store.save(case)
    control = SimpleNamespace(active=lambda: activation, release=lambda _id: None)
    monkeypatch.setattr(live_demo_cases, "FaultStateStore", lambda _path: control)
    monkeypatch.setattr(live_demo_cases, "deactivate_infrastructure_fault", lambda _item: None)
    monkeypatch.setattr(live_demo_cases, "verify_stack_health", lambda: None)

    live_demo_cases.remediate_case(store, case, "Project engineer")

    assert case["status"] == "READY_TO_VERIFY"
    assert case["recovery_result"] is None
    assert case["recovery_verified"] is False

    monkeypatch.setattr(
        live_demo_cases,
        "_transaction",
        lambda run_id, request_id, trace_id: {
            "request_id": request_id,
            "trace_id": trace_id,
            "status_code": 200,
            "latency_ms": 20.0,
            "started_at": live_demo_cases.utc_iso(),
            "ended_at": live_demo_cases.utc_iso(),
            "succeeded": True,
            "body": {"status": "COMPLETED"},
        },
    )
    live_demo_cases.verify_case_recovery(store, case)

    assert case["status"] == "RESOLVED"
    assert case["recovery_result"]["request_id"] != case["request_id"]
    assert case["recovery_verified"] is True


def test_unlinked_live_incident_is_not_claimed_for_the_demo_run(monkeypatch) -> None:
    monkeypatch.setattr(
        live_demo_cases.httpx,
        "post",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200),
    )
    monkeypatch.setattr(
        live_demo_cases.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "incidents": [
                    {"detected_at": "2026-09-27T12:00:01Z", "incident_id": "unrelated"}
                ]
            },
        ),
    )

    assert live_demo_cases._model_analysis("demo-case-123", "2026-09-27T12:00:00Z") is None


def test_http_success_alone_does_not_prove_completed_transaction(monkeypatch) -> None:
    monkeypatch.setattr(
        live_demo_cases.httpx,
        "post",
        lambda *_args, **_kwargs: SimpleNamespace(
            status_code=200,
            is_success=True,
            json=lambda: {"status": "PENDING"},
        ),
    )

    result = live_demo_cases._transaction("demo-1", "request-1", "trace-1")

    assert result["status_code"] == 200
    assert result["succeeded"] is False


def test_hydration_does_not_treat_stack_health_as_recovery(tmp_path) -> None:
    store = DemoCaseStore(tmp_path / "cases.sqlite3")
    case = store.create("SERVICE_UNAVAILABLE")
    case["recovery_verified"] = True
    case["recovery_result"] = None
    store.save(case)

    assert store.get(case["case_id"])["recovery_verified"] is False
