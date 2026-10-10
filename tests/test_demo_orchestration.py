from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.demo_orchestration import (
    DEMO_SCENARIOS,
    DemoFaultControl,
    DemoSessionCreate,
    DemoSessionStore,
    append_demo_event,
    new_session,
    public_session,
)


def test_catalog_has_normal_primary_and_secondary_cases() -> None:
    assert tuple(DEMO_SCENARIOS) == (
        "normal-transfer",
        "payment-service-unavailable",
        "ledger-service-unavailable",
        "account-response-timeout",
        "ledger-response-timeout",
        "account-error-response",
    )
    assert sum(item.recommended for item in DEMO_SCENARIOS.values()) == 3
    assert DEMO_SCENARIOS["ledger-service-unavailable"].expected_funds_status == "COMMITTED"


def test_session_store_persists_ordered_control_events(tmp_path) -> None:
    store = DemoSessionStore(tmp_path)
    session = new_session(
        DemoSessionCreate(scenario_id="ledger-service-unavailable", amount_minor=2500),
        "private-engineer-id",
    )
    store.save(session)

    def update(value):
        value["state"] = "FAULT_ARMED"
        append_demo_event(value, "fault_armed", "READY", "Fault armed")

    stored = asyncio.run(store.mutate(session["session_id"], update))
    reloaded = store.load(session["session_id"])
    assert stored == reloaded
    assert [event["sequence"] for event in reloaded["events"]] == [1, 2]
    assert reloaded["state"] == "FAULT_ARMED"
    assert public_session(reloaded).get("created_by") is None


def test_fault_control_is_exclusive_scoped_and_clearable(tmp_path) -> None:
    control = DemoFaultControl(tmp_path / "active_fault.json")
    first = new_session(DemoSessionCreate(scenario_id="payment-service-unavailable"), "x")
    second = new_session(DemoSessionCreate(scenario_id="ledger-service-unavailable"), "x")
    activation = control.arm(first)
    assert activation["run_id"] == first["session_id"]
    assert control.require_owned(first["session_id"])["fault_id"] == "SERVICE_UNAVAILABLE"
    with pytest.raises(RuntimeError, match="already active"):
        control.arm(second)
    with pytest.raises(RuntimeError, match="another session"):
        control.clear(second["session_id"])
    control.clear(first["session_id"])
    assert control.read() is None


def test_fault_control_removes_expired_activation(tmp_path) -> None:
    path = tmp_path / "active_fault.json"
    path.write_text(
        json.dumps(
            {
                "run_id": "demo-expired",
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert DemoFaultControl(path).read() is None
    assert not path.exists()


def test_normal_scenario_cannot_arm_fault(tmp_path) -> None:
    session = new_session(DemoSessionCreate(scenario_id="normal-transfer"), "x")
    with pytest.raises(ValueError, match="no fault"):
        DemoFaultControl(tmp_path / "active_fault.json").arm(session)
