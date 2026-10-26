"""Offline regression checks for bounded, fail-closed HIGH_LOAD correlation."""
from __future__ import annotations
import asyncio
import pytest
from scripts import faultweave_v2_final_two_capture as cap
from scripts.phase19c2d_fault_pilot import correlation_gate

RID = 'v2pilot-test1234567890'

def row(n, status=200, error=None):
    return {'request_id': f'{RID}-request-{n:05d}', 'trace_id': f'trace-{n}',
            'status_code': status, 'transport_error': error,
            'latency_ms': 150.0, 'expected_outcome': status == 200}

def event(r, trace=None):
    return {'run_id': RID, 'request_id': r['request_id'],
            'trace_id': trace or r['trace_id'], 'service': 'gateway',
            'event_type': 'http_request_completed',
            'timestamp': '2026-10-12T18:37:00+00:00'}

def test_all_events_on_first_fetch(monkeypatch):
    records = [row(1), row(2)]
    calls = []
    def get(*args):
        calls.append(1)
        return [event(r) for r in records]
    monkeypatch.setattr(cap, 'raw_events_for', get)
    observed = asyncio.run(cap.collect_correlation_checked_events(RID, False, records, []))
    correlation_gate(records, observed)
    assert len(observed) == 2 and len(calls) == 1


def test_delayed_logs_recovered_without_bypassing_gate(monkeypatch):
    records = [row(1), row(2)]
    calls = []
    async def no_wait(_):
        pass
    monkeypatch.setattr(cap.asyncio, 'sleep', no_wait)
    def get(*args):
        calls.append(1)
        return [event(records[0])] if len(calls) == 1 else [event(r) for r in records]
    monkeypatch.setattr(cap, 'raw_events_for', get)
    observed = asyncio.run(cap.collect_correlation_checked_events(RID, False, records, []))
    correlation_gate(records, observed)
    assert len(calls) == 2


def test_unobserved_transport_error_fails_closed_with_diagnostics(monkeypatch):
    r = row(825, None, 'ConnectTimeout')
    async def no_wait(_):
        pass
    monkeypatch.setattr(cap.asyncio, 'sleep', no_wait)
    calls = []
    def get(*args):
        calls.append(1)
        return []
    monkeypatch.setattr(cap, 'raw_events_for', get)
    with pytest.raises(RuntimeError, match="transport_error=.*ConnectTimeout") as error:
        asyncio.run(cap.collect_correlation_checked_events(RID, False, [r], []))
    assert 'MISSING_SERVICE_EVIDENCE' in str(error.value)
    assert 'status=None' in str(error.value)
    assert len(calls) == 4


def test_success_without_logs_is_never_accepted(monkeypatch):
    r = row(825)
    async def no_wait(_):
        pass
    monkeypatch.setattr(cap.asyncio, 'sleep', no_wait)
    monkeypatch.setattr(cap, 'raw_events_for', lambda *args: [])
    with pytest.raises(RuntimeError, match='MISSING_SERVICE_EVIDENCE'):
        asyncio.run(cap.collect_correlation_checked_events(RID, False, [r], []))


def test_mismatched_trace_still_rejected(monkeypatch):
    r = row(1)
    monkeypatch.setattr(cap, 'raw_events_for', lambda *args: [event(r, 'wrong-trace')])
    events = asyncio.run(cap.collect_correlation_checked_events(RID, False, [r], []))
    with pytest.raises(RuntimeError, match='Incorrect service trace'):
        correlation_gate([r], events)
