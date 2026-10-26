"""Phase 19B.3 offline unit tests; never call Docker or real services."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.phase19b3_observability import (
    RESEARCH_EDGES, SCENARIOS, assess_acceptance, collect_observable_evidence,
)

START = datetime(2026, 10, 11, 7, 19, 6, tzinfo=UTC)
END = START + timedelta(seconds=60)


def snap():
    return {"window_seconds": 60, "request_observation_source": "client_observed",
            "window_started_at": START.isoformat(), "window_ended_at": END.isoformat(),
            "request_count": 180, "event_count": 800}


def event(service, *, target=None, kind="downstream_request_failed", error="ReadTimeout", status=None,
          run_id="demo-123", timestamp=None):
    return {"run_id": run_id, "timestamp": timestamp or (START + timedelta(seconds=5)).isoformat(),
            "event_type": kind, "service": service, "downstream_service": target,
            "error_type": error, "status_code": status}


class Buffer:
    def __init__(self, events):
        self.events = events
        self.calls = []
    def events_between(self, a, b):
        self.calls.append((a, b))
        return self.events


def acceptance(scenario, evidence=None, decision=True, exact=True):
    return {"selected_scenario": scenario,
            "anomaly_decision_match": decision, "model_match": exact,
            "model": {"status": "EVALUATED", "scope": "challenge_only_telemetry",
                      "window": snap(), "observability": evidence}}


def test_static_contract_excludes_payment_account_route():
    assert "payment->account" not in RESEARCH_EDGES
    assert "payment->ledger" in RESEARCH_EDGES
    assert len(SCENARIOS) == 6


def test_unobservable_timeout_invalid_even_with_normal_ml_prediction():
    result = assess_acceptance(acceptance("account-response-timeout", decision=False, exact=False))
    assert result["status"] == "INVALID"
    assert result["reason"] == "FAULT_NOT_IN_RESEARCH_WORKLOAD_PATH"


def test_collect_filters_other_runs_and_out_of_window_events():
    events = [
        event("payment", target="ledger"),
        event("payment", target="ledger", run_id="unrelated"),
        event("payment", target="ledger", timestamp=(END + timedelta(seconds=1)).isoformat()),
        event("payment", target="ledger", timestamp=START.isoformat()),
    ]
    buffer = Buffer(events)
    record = collect_observable_evidence(buffer, "demo-123", snap())
    assert buffer.calls == [(START, END)]
    assert record["correlated_event_count"] == 1
    assert record["edges"]["payment->ledger"]["timeouts"] == 1
    assert "fault_id" not in record and "selected_scenario" not in record


def test_observed_timeout_and_correct_prediction_accept():
    evidence = collect_observable_evidence(Buffer([event("payment", target="ledger")]), "demo-123", snap())
    audit = assess_acceptance(acceptance("ledger-response-timeout", evidence))
    assert audit["status"] == "ACCEPTED"
    assert audit["observable_fault_confirmed"] is True


def test_observed_timeout_wrong_decision_is_real_mismatch():
    evidence = collect_observable_evidence(Buffer([event("payment", target="ledger")]), "demo-123", snap())
    audit = assess_acceptance(acceptance("ledger-response-timeout", evidence, decision=False, exact=False))
    assert audit["status"] == "MODEL_MISMATCH"


def test_no_correlated_fault_not_counted_as_model_failure():
    evidence = collect_observable_evidence(Buffer([event("payment", target="ledger", kind="downstream_request_completed", error=None, status=200)]), "demo-123", snap())
    audit = assess_acceptance(acceptance("ledger-response-timeout", evidence, decision=False))
    assert audit["status"] == "INVALID"
    assert audit["reason"] == "NO_RUN_CORRELATED_FAULT_EFFECT"


def test_service_unavailable_requires_real_5xx():
    evidence = collect_observable_evidence(Buffer([
        event("payment", kind="http_request_completed", target=None, error=None, status=503)
    ]), "demo-123", snap())
    assert assess_acceptance(acceptance("payment-service-unavailable", evidence))["status"] == "ACCEPTED"


def test_error_burst_requires_real_5xx_not_missing_request():
    evidence = collect_observable_evidence(Buffer([
        event("account", kind="http_request_completed", error=None, status=500)
    ]), "demo-123", snap())
    assert assess_acceptance(acceptance("account-error-response", evidence))["status"] == "ACCEPTED"


def test_normal_scenario_requires_good_decision_and_correlated_evidence():
    evidence = collect_observable_evidence(Buffer([
        event("gateway", kind="http_request_completed", error=None, status=200)
    ]), "demo-123", snap())
    assert assess_acceptance(acceptance("normal-transfer", evidence))["status"] == "ACCEPTED"
    assert assess_acceptance(acceptance("normal-transfer", evidence, decision=False))["status"] == "MODEL_MISMATCH"


def test_missing_observability_fails_closed_for_reachable_fault():
    result = assess_acceptance(acceptance("ledger-response-timeout"))
    assert result["reason"] == "OBSERVABILITY_EVIDENCE_MISSING"


def test_mismatched_window_rejected():
    evidence = collect_observable_evidence(Buffer([event("payment", target="ledger")]), "demo-123", snap())
    evidence["window_ended_at"] = "2026-10-11T00:00:00Z"
    assert assess_acceptance(acceptance("ledger-response-timeout", evidence))["status"] == "INVALID"


def test_wrong_or_missing_input_rejected():
    invalid = snap()
    invalid["window_seconds"] = 10
    with pytest.raises(ValueError, match="validated"):
        collect_observable_evidence(Buffer([]), "demo-123", invalid)
    assert assess_acceptance(acceptance("totally-unknown"))["status"] == "INVALID"
