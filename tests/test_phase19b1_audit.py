"""Phase 19B.1 regression checks. No live requests, jobs, or model writes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasets.final_features import (
    FEATURE_NAMES,
    _event_features as training_event_features,
    _request_features as training_request_features,
)
from app.intelligence_live import (
    event_features as live_event_features,
    request_features as live_request_features,
)
from scripts.audit_phase19b1 import (
    AuditError,
    METADATA_FILES,
    audit,
    extract_snapshot,
    frozen_contract,
    training_statistics,
)

CANDIDATE = "DATABASE_HIGH_LATENCY"


def synthetic_frozen(tmp_path: Path) -> dict:
    # The fixture mirrors the three frozen JSON metadata locations; never loads joblib.
    root = tmp_path / "frozen"
    common = {"window_seconds": 60, "feature_names": list(FEATURE_NAMES)}
    profiles = {CANDIDATE: {
        "centers": [0.0] * len(FEATURE_NAMES),
        "scales": [1.0] * len(FEATURE_NAMES),
    }}
    index = FEATURE_NAMES.index("event_count")
    profiles[CANDIDATE]["scales"][index] = 0.012
    contents = {
        "isolation": common,
        "classifier": {**common, "classes": [CANDIDATE]},
        "support": {**common, "class_support_profiles": profiles,
                    "class_support_limits": {CANDIDATE: 10.8173424}},
    }
    for key, location in METADATA_FILES.items():
        path = root / location
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(contents[key]), encoding="utf-8")
    return frozen_contract(root)


def snapshot(**overrides) -> dict:
    values = {name: 0.0 for name in FEATURE_NAMES}
    values["event_count"] = 59.0  # 59 / .012 == 4916.666...
    values["request_count"] = 0.0
    values.update(overrides)
    return {"window_seconds": 60, "request_observation_source": "client_observed",
            "coverage_seconds": 65.0, "request_count": values["request_count"],
            "event_count": values["event_count"], "features": values}


def test_exact_65_order_and_score_cause(tmp_path: Path) -> None:
    frozen = synthetic_frozen(tmp_path)
    assert len(FEATURE_NAMES) == 65
    result = audit(snapshot(), frozen, CANDIDATE, expected_score=4916.66666667,
                   expected_limit=10.8173424)
    assert result["responsible_features"] == ["event_count"]
    assert result["support_decision"] == "REJECT"
    assert result["hidden_fault_label_used"] is False


def test_missing_extra_non_numeric_or_wrong_source_is_blocked(tmp_path: Path) -> None:
    frozen = synthetic_frozen(tmp_path)
    malformed = snapshot()
    malformed["features"].pop("request_count")
    with pytest.raises(AuditError, match="feature name mismatch"):
        audit(malformed, frozen, CANDIDATE)
    malformed = snapshot()
    malformed["features"]["request_count"] = "0"
    with pytest.raises(AuditError, match="nonnumeric"):
        audit(malformed, frozen, CANDIDATE)
    malformed = snapshot()
    malformed["features"]["request_success_rate"] = 2.0
    with pytest.raises(AuditError, match="fraction"):
        audit(malformed, frozen, CANDIDATE)
    malformed = snapshot()
    malformed["request_observation_source"] = "gateway_derived"
    with pytest.raises(AuditError, match="client_observed"):
        audit(malformed, frozen, CANDIDATE)


def test_score_mismatch_and_absent_vector_fail_closed(tmp_path: Path) -> None:
    frozen = synthetic_frozen(tmp_path)
    with pytest.raises(AuditError, match="support score mismatch"):
        audit(snapshot(), frozen, CANDIDATE, expected_score=1.0)
    with pytest.raises(AuditError, match="no 65-feature"):
        extract_snapshot({"classification": {"class_support_score": 4916.66},
                          "selected_scenario": "hidden-do-not-use"})


def test_training_statistics_only_fitting_known_rows(tmp_path: Path) -> None:
    features = {name: 0.0 for name in FEATURE_NAMES}
    path = tmp_path / "training.jsonl"
    base = {"split": "train", "label": CANDIDATE,
            "scenario_type": "known_fault", "interval": "fault",
            "training_eligible": True, "window_seconds": 60,
            "features": features}
    unrelated = {**base, "split": "evaluation_only", "features": {**features, "event_count": 5000.0}}
    path.write_text(json.dumps(base) + "\n" + json.dumps(unrelated) + "\n", encoding="utf-8")
    stats, count = training_statistics(path, list(FEATURE_NAMES), CANDIDATE)
    assert count == 1
    assert stats["event_count"]["maximum"] == 0.0


@pytest.mark.parametrize("requests,events", [
    ([], []),
    ([{"status_code": 200, "expected_outcome": True, "scenario": "valid",
       "latency_ms": 15.0, "transport_error": None}],
     [{"service": "gateway", "event_type": "http_request_completed", "level": "INFO",
       "success": True, "latency_ms": 15.0, "status_code": 200,
       "downstream_service": "payment", "error_type": None}]),
    ([{"status_code": 503, "expected_outcome": False, "scenario": "valid",
       "latency_ms": 105.4, "transport_error": None},
      {"status_code": None, "expected_outcome": False, "scenario": "invalid_account",
       "latency_ms": None, "transport_error": "reset"}],
     [{"service": "payment", "event_type": "dependency_failure", "level": "ERROR",
       "success": False, "latency_ms": 300.0, "status_code": 503,
       "downstream_service": "account", "error_type": "connection_reset"},
      {"service": "ledger", "event_type": "dependency_timeout", "level": "CRITICAL",
       "success": False, "latency_ms": None, "status_code": None,
       "downstream_service": "ledger", "error_type": "downstream_timeout"}]),
])
def test_training_live_aggregation_semantics_probes(requests: list[dict], events: list[dict]) -> None:
    training = {**training_request_features(requests, 60.0),
                **training_event_features(events, 60.0)}
    live = {**live_request_features(requests, 60.0),
            **live_event_features(events, 60.0)}
    assert tuple(training) == FEATURE_NAMES
    assert tuple(live) == FEATURE_NAMES
    assert training == live, {key: (training[key], live[key])
                              for key in FEATURE_NAMES if training[key] != live[key]}
