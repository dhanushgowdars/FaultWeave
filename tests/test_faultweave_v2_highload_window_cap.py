"""Offline regressions: 16-RPS client-drain window cap, legacy audits, fail-closed gating."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pytest
from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
from scripts import faultweave_v2_final_two_capture as collector
from scripts import faultweave_v2_final_two_audit as audit

UTC = timezone.utc
RID = 'v2train-aaaabbbbccccdddd'

def sample():
    phase_start = datetime(2026, 10, 12, 18, 40, tzinfo=UTC)
    # Task scheduling spans 75s at 16 RPS, but waiting for all responses
    # can push the true collection completion past the last request start.
    actual_phase_end = phase_start + timedelta(seconds=91)
    reqs = []
    events = []
    for i in range(1200):
        stamp = phase_start + timedelta(seconds=i / 16)
        rid = f'{RID}-request-{i+1:05d}'
        reqs.append(dict(run_id=RID, request_id=rid, started_at=stamp.isoformat(),
                         latency_ms=250.0, scenario='valid', status_code=200,
                         expected_outcome=True, transport_error=None))
        events.append(dict(run_id=RID, request_id=rid,
                           timestamp=(stamp+timedelta(milliseconds=100)).isoformat(),
                           service='gateway', event_type='http_request_completed',
                           status_code=200, latency_ms=250.0, success=True,
                           level='INFO', error_type=None, downstream_service=None))
    return phase_start, actual_phase_end, reqs, events

def window(start, end):
    return select_exact_window(phase_started_at=start, phase_ended_at=end,
        telemetry_watermark=end+timedelta(seconds=10),
        fault_activated_at=start-timedelta(seconds=1),
        fault_expires_at=start+timedelta(seconds=145),
        fault_cleared_at=end+timedelta(seconds=1),
        fault_required=True, lead_guard_seconds=2, tail_guard_seconds=2)

def test_16rps_slow_response_drain_boundary_preserved():
    start, end, reqs, events = sample()
    old_window = window(start, end)
    with pytest.raises(ValueError, match='request observations do not span the full window'):
        build_observable_features(run_id=RID, window=old_window,
                                  requests=reqs, events=events, require_boundary_evidence=True)
    cap = collector.high_load_window_phase_end(end, reqs)
    assert cap < end
    revised_window = window(start, cap)
    assert revised_window.window_seconds == 60
    assert revised_window.ended_at <= end
    vector = build_observable_features(run_id=RID, window=revised_window,
                            requests=reqs, events=events, require_boundary_evidence=True)
    assert len(vector) == 65
    assert vector['request_count'] > 900
    assert vector['request_rate_per_second'] > 15
    metadata = {'window_selection_strategy':'high-load-last-client-start-cap-v1',
                'window_phase_ended_at':cap.isoformat()}
    assert audit.verified_window_phase_end(metadata,'HIGH_LOAD',end,reqs) == cap

def test_existing_legacy_capture_keeps_old_window_selection():
    _, end, reqs, _ = sample()
    assert audit.verified_window_phase_end({},'HIGH_LOAD',end,reqs) == end
    assert audit.verified_window_phase_end({},'DATABASE_UNAVAILABLE',end,reqs) == end

def test_tampered_cap_or_unknown_policy_rejected():
    _, end, reqs, _ = sample()
    cap = collector.high_load_window_phase_end(end,reqs)
    metadata = {'window_selection_strategy':'high-load-last-client-start-cap-v1',
                'window_phase_ended_at':cap.isoformat()}
    with pytest.raises(ValueError,match='mismatch'):
        audit.verified_window_phase_end({**metadata,'window_phase_ended_at':end.isoformat()},'HIGH_LOAD',end,reqs)
    with pytest.raises(ValueError,match='unrecognized'):
        audit.verified_window_phase_end({**metadata,'window_selection_strategy':'unsafe'},'HIGH_LOAD',end,reqs)
    with pytest.raises(ValueError,match='unrecognized'):
        audit.verified_window_phase_end(metadata,'DATABASE_UNAVAILABLE',end,reqs)
    with pytest.raises(ValueError,match='without strategy'):
        audit.verified_window_phase_end({'window_phase_ended_at':cap.isoformat()},'HIGH_LOAD',end,reqs)

def test_no_invented_requests_and_fail_closed_lease_constraints():
    start, end, reqs, events = sample()
    with pytest.raises(RuntimeError, match='no genuine'):
        collector.high_load_window_phase_end(end, [])
    with pytest.raises(RuntimeError, match='after phase end'):
        collector.high_load_window_phase_end(start, reqs)
    cap = collector.high_load_window_phase_end(end, reqs)
    with pytest.raises(ValueError,match='insufficient 60-second'):
        select_exact_window(phase_started_at=start,phase_ended_at=cap,
             telemetry_watermark=cap, fault_activated_at=start,
             fault_expires_at=start + timedelta(seconds=25),
             fault_cleared_at=end, fault_required=True)
