"""Fail-closed Phase 19C.2B candidate 60-second feature-contract smoke tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from datasets.phase19c2b_exact_windows import (
    ExactWindow,
    V2WindowContractError,
    _runtime_eligible,
    build_observable_features,
    select_exact_window,
)


def t(second: float = 0) -> datetime:
    return datetime(2026, 10, 11, 9, 0, tzinfo=UTC) + timedelta(seconds=second)


def window(fault_required=True, **updates):
    args = dict(
        phase_started_at=t(5), phase_ended_at=t(80),
        telemetry_watermark=t(79), fault_required=fault_required,
    )
    if fault_required:
        args.update(
            fault_activated_at=t(2), fault_expires_at=t(100),
            fault_cleared_at=t(80),
        )
    args.update(updates)
    return select_exact_window(**args)


def test_valid_fault_window_uses_exact_60_seconds_and_guards():
    result = window()
    assert result.started_at == t(18)
    assert result.ended_at == t(78)
    assert (result.ended_at - result.started_at).total_seconds() == 60


def test_actual_expiry_is_respected():
    result = window(fault_expires_at=t(73))
    assert result.ended_at == t(71)
    assert result.started_at == t(11)


def test_short_fault_is_rejected_rather_than_padded():
    with pytest.raises(V2WindowContractError, match="insufficient 60-second"):
        window(fault_expires_at=t(58))


def test_short_watermark_is_rejected():
    with pytest.raises(V2WindowContractError, match="insufficient 60-second"):
        window(telemetry_watermark=t(60))


def test_fault_requires_arm_expiry_and_cleanup_proof():
    with pytest.raises(V2WindowContractError, match="arm, expiry and cleanup"):
        window(fault_cleared_at=None)


def test_normal_window_works_without_fault_proof():
    result = window(fault_required=False)
    assert result.started_at == t(18)
    assert result.ended_at == t(78)


def test_normal_window_rejects_fault_metadata():
    with pytest.raises(V2WindowContractError, match="normal window"):
        window(fault_required=False, fault_activated_at=t(2))


def test_invalid_timezone_and_window_length():
    with pytest.raises(V2WindowContractError, match="timezone-aware"):
        window(phase_started_at=datetime(2026, 10, 11, 9, 0))
    with pytest.raises(V2WindowContractError, match="exactly 60"):
        ExactWindow(t(1), t(62))


def records(result):
    requests = []
    events = []
    for sec in (1, 30, 59):
        timestamp = result.started_at + timedelta(seconds=sec)
        requests.append(dict(run_id="r1", request_id=f"r{sec}", started_at=timestamp.isoformat(),
                             latency_ms=11.0, status_code=200, expected_outcome=True,
                             transport_error=None, scenario="valid"))
        events.append(dict(run_id="r1", request_id=f"r{sec}", timestamp=timestamp.isoformat(),
                           service="gateway", event_type="http_request_completed", path="/api/v1/transactions",
                           level="INFO", success=True, status_code=200, latency_ms=12.0,
                           downstream_service=None, error_type=None))
    return requests, events


def test_feature_contract_65_matches_training_and_live_functions():
    from datasets.final_features import FEATURE_NAMES
    from app.intelligence_live import request_features, event_features, eligible_runtime_event

    w = window()
    requests, events = records(w)
    assert eligible_runtime_event(events[0]) == _runtime_eligible(events[0])
    features = build_observable_features(run_id="r1", window=w, requests=requests, events=events)
    expected = request_features(requests, 60.0)
    expected.update(event_features(events, 60.0))
    assert len(features) == 65
    assert tuple(features) == FEATURE_NAMES
    assert features == expected
    assert features["request_count"] == 3
    assert features["request_rate_per_second"] == 0.05


def test_wrong_run_and_outside_window_excluded():
    w = window()
    requests, events = records(w)
    requests.append({**requests[0], "run_id": "other", "request_id": "other"})
    events.append({**events[0], "run_id": "other"})
    requests.append({**requests[0], "started_at": w.started_at.isoformat(), "request_id": "boundary"})
    events.append({**events[0], "timestamp": w.started_at.isoformat()})
    features = build_observable_features(run_id="r1", window=w, requests=requests, events=events)
    assert features["request_count"] == 3
    assert features["event_count"] == 3


def test_health_and_intelligence_events_are_excluded():
    w = window()
    requests, events = records(w)
    events += [{**events[0], "event_type": "health_check_completed", "path": "/health/ready"},
               {**events[0], "event_type": "intelligence_model_evaluated"}]
    features = build_observable_features(run_id="r1", window=w, requests=requests, events=events)
    assert features["event_count"] == 3


def test_duplicate_client_requests_fail_closed():
    w = window()
    requests, events = records(w)
    with pytest.raises(V2WindowContractError, match="duplicate"):
        build_observable_features(run_id="r1", window=w, requests=requests + requests[:1], events=events)


def test_duplicate_events_follow_live_deduplication():
    w = window()
    requests, events = records(w)
    features = build_observable_features(run_id="r1", window=w, requests=requests, events=events + events[:1])
    assert features["event_count"] == 3


def test_missing_run_id_fails_closed():
    w = window()
    requests, events = records(w)
    del requests[0]["run_id"]
    with pytest.raises(V2WindowContractError, match="missing run_id"):
        build_observable_features(run_id="r1", window=w, requests=requests, events=events)


def test_edge_coverage_rejects_one_sided_observations():
    w = window()
    requests, events = records(w)
    with pytest.raises(V2WindowContractError, match="request observations do not span"):
        build_observable_features(run_id="r1", window=w,
                                  requests=[requests[1]], events=events)


def test_no_observed_events_fails_closed():
    w = window()
    requests, _ = records(w)
    with pytest.raises(V2WindowContractError, match="lacks real"):
        build_observable_features(run_id="r1", window=w, requests=requests, events=[])
