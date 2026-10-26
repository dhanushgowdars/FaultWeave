"""Phase 19B.3: observational workload evidence and POST-REVEAL diagnostic validity.

Never use scenario identity or fault-control metadata to generate telemetry evidence.
The recorded evidence is derived only from the same timestamp-bounded model window.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

CONTRACT_VERSION = "19b3.1"
WORKLOAD_PATH = "/api/v1/transactions"
# Verified against the original research transaction route and its downstream calls.
# Edges are not inferred from the selected fault. Changes require code review.
RESEARCH_EDGES = frozenset({
    "gateway->authentication", "gateway->account", "gateway->transaction",
    "transaction->account", "transaction->payment", "transaction->ledger",
    "payment->ledger",
})
SCENARIOS = {
    "normal-transfer": (None, None),
    "payment-service-unavailable": ("SERVICE_UNAVAILABLE", "payment"),
    "ledger-service-unavailable": ("SERVICE_UNAVAILABLE", "ledger"),
    "account-response-timeout": ("DOWNSTREAM_TIMEOUT", "payment->account"),
    "ledger-response-timeout": ("DOWNSTREAM_TIMEOUT", "payment->ledger"),
    "account-error-response": ("DOWNSTREAM_ERROR_BURST", "account"),
}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("telemetry timestamp must include timezone")
    return parsed.astimezone(UTC)


def _status(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def collect_observable_evidence(buffer: Any, run_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Capture generic, observable per-edge outcomes from the exact model window.

    This function never accepts ground-truth scenario IDs or fault-control state.
    No raw payloads, personal information, credentials or protected labels are stored.
    """
    if not run_id or not isinstance(run_id, str):
        raise ValueError("run ID is required for correlated evidence")
    if snapshot.get("window_seconds") != 60 or snapshot.get("request_observation_source") != "client_observed":
        raise ValueError("requires the exact validated 60-second client-observed window")
    start = _time(snapshot["window_started_at"])
    end = _time(snapshot["window_ended_at"])
    if (end - start).total_seconds() != 60:
        raise ValueError("cannot substitute a non-60s evidence window")
    records = buffer.events_between(start, end)
    edges: dict[str, dict[str, int]] = {}
    services: dict[str, dict[str, int]] = {}
    correlated = 0
    for item in records:
        if item.get("run_id") != run_id:
            continue
        # Re-validate boundaries; do not trust third-party buffer implementations.
        try:
            when = _time(item["timestamp"])
        except (ValueError, TypeError, KeyError):
            continue
        if not (start < when <= end):
            continue
        correlated += 1
        service = str(item.get("service") or "")
        event_type = str(item.get("event_type") or "")
        downstream = str(item.get("downstream_service") or "")
        status = _status(item.get("status_code"))
        error = str(item.get("error_type") or "").lower()
        if service and event_type in {"http_request_completed", "http_request_failed"}:
            cell = services.setdefault(service, {"requests": 0, "http_5xx": 0})
            cell["requests"] += 1
            if status is not None and status >= 500:
                cell["http_5xx"] += 1
        if service and downstream and event_type in {"downstream_request_completed", "downstream_request_failed"}:
            cell = edges.setdefault(f"{service}->{downstream}", {
                "calls": 0, "timeouts": 0, "http_5xx": 0, "transport_failures": 0
            })
            cell["calls"] += 1
            if event_type == "downstream_request_failed":
                cell["transport_failures"] += 1
                if "timeout" in error or "timed_out" in error:
                    cell["timeouts"] += 1
            elif status is not None and status >= 500:
                cell["http_5xx"] += 1
    return {
        "schema_version": CONTRACT_VERSION,
        "workload_path": WORKLOAD_PATH,
        "window_started_at": snapshot["window_started_at"],
        "window_ended_at": snapshot["window_ended_at"],
        "window_seconds": 60,
        "request_count": snapshot.get("request_count"),
        "event_count": snapshot.get("event_count"),
        "correlated_event_count": correlated,
        "edges": edges,
        "services": services,
        "evidence_basis": "run_correlated_structured_events_in_exact_model_window",
        # Deliberately NO scenario_id, fault_id, target, nonce or protected ground truth.
    }


def _unobservable(scenario: str) -> bool:
    kind, target = SCENARIOS[scenario]
    if kind is None:
        return False
    if kind == "DOWNSTREAM_TIMEOUT":
        return target not in RESEARCH_EDGES
    return not any(edge.endswith("->" + str(target)) for edge in RESEARCH_EDGES)


def assess_acceptance(acceptance: dict[str, Any]) -> dict[str, Any]:
    """Post-reveal validity gate; NOT used to infer anomaly/class labels.

    A failed ML prediction counts as a model failure only when a fault mechanism was
    genuinely observed in the same feature window. Otherwise test is invalid.
    """
    scenario = acceptance.get("selected_scenario")
    model = acceptance.get("model") or {}
    evidence = model.get("observability") or {}
    result = {
        "schema_version": CONTRACT_VERSION,
        "status": "INVALID",
        "reason": "UNKNOWN_SCENARIO",
        "workload_path": WORKLOAD_PATH,
        "model_evaluated": model.get("status") == "EVALUATED",
        "anomaly_decision_match": acceptance.get("anomaly_decision_match") is True,
        "exact_class_match": acceptance.get("model_match") is True,
        "observable_fault_confirmed": False,
    }
    if scenario not in SCENARIOS:
        return result
    fault_kind, target = SCENARIOS[scenario]
    # Static path compatibility can be established even for older saved runs.
    if _unobservable(scenario):
        result["reason"] = "FAULT_NOT_IN_RESEARCH_WORKLOAD_PATH"
        return result
    if model.get("status") != "EVALUATED" or model.get("scope") != "challenge_only_telemetry":
        result["reason"] = "NO_VALID_CHALLENGE_ONLY_MODEL"
        return result
    window = model.get("window") or {}
    if not evidence or evidence.get("schema_version") != CONTRACT_VERSION:
        result["reason"] = "OBSERVABILITY_EVIDENCE_MISSING"
        return result
    if (
        evidence.get("window_seconds") != 60
        or evidence.get("window_started_at") != window.get("window_started_at")
        or evidence.get("window_ended_at") != window.get("window_ended_at")
        or evidence.get("workload_path") != WORKLOAD_PATH
        or int(evidence.get("correlated_event_count") or 0) <= 0
        or int(evidence.get("request_count") or 0) <= 0
    ):
        result["reason"] = "INSUFFICIENT_CORRELATED_WINDOW_EVIDENCE"
        return result
    if fault_kind == "DOWNSTREAM_TIMEOUT":
        observed = int((evidence.get("edges") or {}).get(target, {}).get("timeouts") or 0)
    elif fault_kind in {"SERVICE_UNAVAILABLE", "DOWNSTREAM_ERROR_BURST"}:
        service_5xx = int((evidence.get("services") or {}).get(target, {}).get("http_5xx") or 0)
        edge_5xx = sum(
            int(cell.get("http_5xx") or 0)
            for edge, cell in (evidence.get("edges") or {}).items()
            if edge.endswith("->" + str(target)) and isinstance(cell, dict)
        )
        observed = max(service_5xx, edge_5xx)
    else:
        observed = 1  # NORMAL: no injected effect is required.
    result["observable_fault_confirmed"] = observed > 0 if fault_kind else None
    if fault_kind and observed == 0:
        result["reason"] = "NO_RUN_CORRELATED_FAULT_EFFECT"
        return result
    if not result["anomaly_decision_match"]:
        result["status"] = "MODEL_MISMATCH"
        result["reason"] = "ANOMALY_DECISION_INCORRECT"
        return result
    if not result["exact_class_match"]:
        result["status"] = "MODEL_MISMATCH"
        result["reason"] = "EXACT_CLASS_INCORRECT"
        return result
    result["status"] = "ACCEPTED"
    result["reason"] = "NORMAL_CORRECT" if fault_kind is None else "OBSERVED_FAULT_CORRECTLY_CLASSIFIED"
    return result
