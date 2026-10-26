"""Offline-only test suite; no Docker, network, database or fault-control mutations."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.phase19c2d_fault_pilot import (
    checked_rps, correlation_gate, execute_fault_phase, fault_evidence_gate,
    new_plan, parsed_time, recovery_gate, main, FAULT_ID, FAULT_TARGET,
    LEASE_SECONDS,
)

RUN = "v2fault-0123456789abcdef"
START = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
WINDOW = SimpleNamespace(started_at=START, ended_at=START + timedelta(seconds=60))


def mkreq(i, status=200, start=None):
    t = start or (START + timedelta(seconds=i / 2))
    return {"run_id": RUN, "request_id": f"{RUN}-request-{i:05d}",
            "trace_id": f"trace-{i}", "started_at": t.isoformat(),
            "status_code": status, "transport_error": None,
            "expected_outcome": status == 200, "scenario": "valid"}


def mkevent(req, status=500, *, service="payment", path="/internal/v1/payments"):
    return {"run_id": RUN, "request_id": req["request_id"],
            "trace_id": req["trace_id"],
            "timestamp": (parsed_time(req["started_at"]) + timedelta(milliseconds=15)).isoformat(),
            "service": service, "event_type": "http_request_completed",
            "path": path, "status_code": status}


def test_dry_run_no_side_effects(monkeypatch, capsys):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("Docker invoked"))
    assert main(["--dry-run"]) == 0
    assert "no Docker" in capsys.readouterr().out


def test_rate_limits():
    assert checked_rps(2.0) == 2.0
    for x in [0, 1.0, 2.01, float("inf"), float("nan")]:
        with pytest.raises(ValueError): checked_rps(x)


def test_localhost_only():
    with pytest.raises(ValueError): main(["--dry-run", "--gateway-url", "http://example.com:18110"])


def test_plans_nonoverlapping_request_id_sequences():
    from experiments.runner import PlannedRequest
    workload = new_plan(RUN, 2.0)
    recovery = new_plan(RUN, 2.0, recovery=True)
    assert len(workload) == 150 and len(recovery) == 8
    assert isinstance(workload[0], PlannedRequest)
    assert {x.sequence for x in workload}.isdisjoint({x.sequence for x in recovery})
    assert workload[-1].offset_seconds < 75


class FakeStore:
    def __init__(self):
        self.released = []
        self.activation = SimpleNamespace(activation_id="abcd", expires_at=START+timedelta(seconds=105))
        self.acquired = []
    def acquire(self, **kw):
        self.acquired.append(kw)
        self.activation.expires_at = datetime.now(UTC) + timedelta(seconds=105)
        return self.activation
    def active(self):
        return self.activation
    def release(self, x):
        self.released.append(x)


def test_fault_lease_release_success():
    s = FakeStore()
    async def fake_executor(*args):
        return [{"ok": True}]
    out = asyncio.run(execute_fault_phase(store=s, run_id=RUN, plan=[],
                                          gateway_url="http://127.0.0.1:18110", executor=fake_executor))
    assert s.released == ["abcd"]
    assert s.acquired[0]["fault_id"] == FAULT_ID
    assert s.acquired[0]["target"] == FAULT_TARGET
    assert s.acquired[0]["duration_seconds"] == LEASE_SECONDS
    assert out[0] == [{"ok":True}]


def test_fault_lease_release_after_exception():
    s = FakeStore()
    async def fake_executor(*args):
        raise RuntimeError("expected mocked failure")
    with pytest.raises(RuntimeError, match="mocked failure"):
        asyncio.run(execute_fault_phase(store=s, run_id=RUN, plan=[],
                                        gateway_url="http://127.0.0.1:18110", executor=fake_executor))
    assert s.released == ["abcd"]


def test_fault_lease_release_after_cancelled():
    s = FakeStore()
    async def fake_executor(*args):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute_fault_phase(store=s, run_id=RUN, plan=[],
                                        gateway_url="http://127.0.0.1:18110", executor=fake_executor))
    assert s.released == ["abcd"]


def test_fault_lease_must_be_owned():
    s = FakeStore()
    s.active = lambda: None
    async def fail_if_called(*args): pytest.fail("should not execute")
    with pytest.raises(RuntimeError, match="ownership"):
        asyncio.run(execute_fault_phase(store=s, run_id=RUN, plan=[],
                                        gateway_url="http://127.0.0.1:18110", executor=fail_if_called))
    assert s.released == ["abcd"]


def test_correlation_accepts_fault_responses():
    rows = [mkreq(1, 503)]
    correlation_gate(rows,[mkevent(rows[0])])


def test_correlation_rejects_trace_mismatch():
    rows = [mkreq(1,503)]
    e = mkevent(rows[0]);e["trace_id"]="wrong"
    with pytest.raises(RuntimeError, match="Incorrect service trace"):
        correlation_gate(rows,[e])


def test_correlation_rejects_missing_events():
    with pytest.raises(RuntimeError, match="Missing service events"):
        correlation_gate([mkreq(1)], [mkevent(mkreq(2))])


def test_window_fault_evidence_accepts_impact():
    rows=[mkreq(i, 503 if i % 2 else 200) for i in range(1, 121)]
    events=[mkevent(r) for r in rows if r["status_code"]==503]
    result=fault_evidence_gate(WINDOW,rows,events)
    assert result["in_window_requests"]==120
    assert result["affected_client_requests"]==60
    assert result["correlated_payment_500_events"]==60


def test_window_fault_evidence_rejects_wrong_dependency():
    rows=[mkreq(i,503 if i%2 else 200) for i in range(1,121)]
    events=[mkevent(r,service="ledger") for r in rows if r["status_code"]==503]
    with pytest.raises(RuntimeError, match="FAULT_NOT_OBSERVABLE"):
        fault_evidence_gate(WINDOW,rows,events)


def test_window_fault_evidence_rejects_wrong_trace():
    rows=[mkreq(i,503 if i%2 else 200) for i in range(1,121)]
    events=[{**mkevent(r),"trace_id":"wrong"} for r in rows if r["status_code"]==503]
    with pytest.raises(RuntimeError, match="FAULT_NOT_OBSERVABLE"):
        fault_evidence_gate(WINDOW,rows,events)


def test_window_fault_evidence_rejects_only_historical_events():
    rows=[mkreq(i,503 if i%2 else 200) for i in range(1,121)]
    events=[{**mkevent(r),"timestamp":(WINDOW.started_at-timedelta(seconds=2)).isoformat()}
            for r in rows if r["status_code"]==503]
    with pytest.raises(RuntimeError, match="FAULT_NOT_OBSERVABLE"):
        fault_evidence_gate(WINDOW,rows,events)


def test_window_fault_evidence_rejects_too_few_requests():
    rows=[mkreq(i,503) for i in range(1,10)]
    with pytest.raises(RuntimeError, match="FAULT_NOT_EVALUABLE"):
        fault_evidence_gate(WINDOW,rows,[mkevent(r) for r in rows])


def test_recovery_after_cleanup_and_same_run():
    clear = START+timedelta(seconds=65)
    rows=[mkreq(i,status=200,start=clear+timedelta(seconds=i)) for i in range(151,159)]
    assert recovery_gate(rows,[mkevent(r) for r in rows],cleared_at=clear)["successful_requests"]==8


def test_recovery_rejects_failed_request():
    clear=START+timedelta(seconds=65)
    rows=[mkreq(i,status=500,start=clear+timedelta(seconds=i)) for i in range(151,159)]
    with pytest.raises(RuntimeError,match="RECOVERY_NOT_VERIFIED"):
        recovery_gate(rows,[mkevent(r) for r in rows],cleared_at=clear)


def test_recovery_rejects_early_request():
    clear=START+timedelta(seconds=65)
    rows=[mkreq(i,status=200,start=clear) for i in range(151,159)]
    with pytest.raises(RuntimeError,match="before fault cleanup"):
        recovery_gate(rows,[mkevent(r) for r in rows],cleared_at=clear)


def test_protected_evidence_not_in_feature_input():
    from scripts.phase19c2d_fault_pilot import FAULT_ID, json_bytes
    doc=json.loads(json_bytes({"features":{"request_count":120},"label":None}))
    assert FAULT_ID not in json.dumps(doc)
