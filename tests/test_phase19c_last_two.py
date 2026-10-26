"""Offline-only tests for the final two known-family diagnostic adapters."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import phase19c_last_two_pilot as p
from scripts import phase19c2k_collection_controller as k


def generated(n=90, latency=200, failed=0):
    start = datetime.now(UTC)
    window = SimpleNamespace(started_at=start, ended_at=start + timedelta(seconds=60))
    requests, events = [], []
    for i in range(n):
        stamp = (start + timedelta(seconds=1 + i * 58 / max(n - 1, 1))).isoformat()
        rid = f'run-request-{i:05d}'
        trace = f'trace-{i}'
        bad = i < failed
        requests.append({'request_id': rid, 'trace_id': trace, 'started_at': stamp,
                         'status_code': 503 if bad else 200, 'transport_error': None,
                         'latency_ms': latency})
        events.append({'request_id': rid, 'trace_id': trace, 'timestamp': stamp,
                       'service': 'gateway', 'event_type': 'http_request_completed',
                       'success': not bad, 'status_code': 503 if bad else 200})
    return window, requests, events


@pytest.mark.parametrize('fault,target,rps', [('DATABASE_UNAVAILABLE','postgresql',1.5),
                                               ('HIGH_LOAD','gateway',8.0)])
def test_exact_configs(fault,target,rps):
    p.validate_configuration(fault,target,rps)


@pytest.mark.parametrize('fault,target,rps', [('DATABASE_UNAVAILABLE','payment',1.5),
    ('DATABASE_UNAVAILABLE','postgresql',2), ('HIGH_LOAD','gateway',1.5),
    ('HIGH_LOAD','payment',8), ('HIGH_LOAD','gateway',100),
    ('DATABASE_HIGH_LATENCY','payment',1.5), ('HIGH_LOAD','gateway',float('nan'))])
def test_reject_wrong_configs(fault,target,rps):
    with pytest.raises(ValueError):
        p.validate_configuration(fault,target,rps)


def test_postgres_effect_requires_real_stop():
    w,r,e = generated(failed=80)
    with pytest.raises(RuntimeError,match='physical'):
        p.effect_gate(w,r,e,'DATABASE_UNAVAILABLE',stop_proof=None,reference=None)
    assert p.effect_gate(w,r,e,'DATABASE_UNAVAILABLE',stop_proof={'docker_running_during_fault':False},reference=None)['correlated_failure_clients'] == 80


def test_postgres_effect_requires_correlated_failures():
    w,r,e = generated(failed=80)
    for item in e:
        item['trace_id']='invalid'
    with pytest.raises(RuntimeError,match='not observable'):
        p.effect_gate(w,r,e,'DATABASE_UNAVAILABLE',stop_proof={'docker_running_during_fault':False},reference=None)


def test_high_volume_not_claimed_as_overload():
    w,r,e=generated(n=480,latency=220,failed=0)
    with pytest.raises(RuntimeError,match='ELEVATED_TRAFFIC_ONLY'):
        p.effect_gate(w,r,e,'HIGH_LOAD',stop_proof=None,reference={'reference_p95_ms':299})


def test_high_load_proven_by_latency_and_correlation():
    w,r,e=generated(n=480,latency=825,failed=0)
    result=p.effect_gate(w,r,e,'HIGH_LOAD',stop_proof=None,reference={'reference_p95_ms':299})
    assert result['observed_overload'] is True
    assert result['gateway_correlated_requests'] == 480


def test_high_load_proven_by_real_failures():
    w,r,e=generated(n=480,latency=220,failed=30)
    result=p.effect_gate(w,r,e,'HIGH_LOAD',stop_proof=None,reference={'reference_p95_ms':299})
    assert result['correlated_failure_clients'] == 30


def test_dry_run_does_not_call_docker(monkeypatch):
    monkeypatch.setattr(p,'blocked_plan_gate',lambda:None)
    monkeypatch.setattr(p,'docker',lambda *a,**k: (_ for _ in ()).throw(AssertionError('Docker called')))
    assert p.main(['--dry-run','--fault','DATABASE_UNAVAILABLE','--target','postgresql','--rps','1.5']) == 0


def test_outage_requires_explicit_consent(monkeypatch):
    monkeypatch.setattr(p,'blocked_plan_gate',lambda:None)
    with pytest.raises(ValueError,match='acknowledge-local-db-outage'):
        p.main(['--execute','--fault','DATABASE_UNAVAILABLE','--target','postgresql',
                '--rps','1.5','--acknowledge-diagnostic-only'])


def test_supervisor_maps_two_new_kinds():
    db = k.choose_adapter('database-unavailable','postgresql',1.5)
    load = k.choose_adapter('high-load','gateway',8.0)
    assert db.fault == 'DATABASE_UNAVAILABLE'
    assert load.fault == 'HIGH_LOAD'
    assert '--acknowledge-local-db-outage' in db.command(execute=True)
    assert '--acknowledge-local-db-outage' not in load.command(execute=True)
    assert '--acknowledge-local-db-outage' not in db.command(execute=False)
    assert k.RUN_RE['high-load'].fullmatch('v2finaltwo-'+'a'*16)
    assert '--acknowledge-local-db-outage' in k.Adapter('database-unavailable','scripts.phase19c_last_two_pilot','data/datasets/v2/pilot-fault','postgresql',1.5,'DATABASE_UNAVAILABLE').command(execute=True)


def test_no_unbounded_rates_via_supervisor():
    with pytest.raises(ValueError):
        k.choose_adapter('high-load','gateway',20)
    with pytest.raises(ValueError):
        k.choose_adapter('database-unavailable','postgresql',8)


def test_postgres_restored_if_workload_raises(monkeypatch):
    restored = []
    monkeypatch.setattr(p,'blocked_plan_gate',lambda:None)
    monkeypatch.setattr(p,'require_clean_local_stack',lambda:None)
    monkeypatch.setattr(p,'preflight_database_outage',lambda:'dummy-container')
    monkeypatch.setattr(p,'stop_local_postgres',lambda container:{'docker_running_during_fault':False})
    monkeypatch.setattr(p,'restore_local_postgres',lambda:restored.append('up'))
    class FakeStore:
        def __init__(self,*args): pass
        def acquire(self,**kwargs):
            return SimpleNamespace(activation_id='id',expires_at=datetime.now(UTC)+timedelta(seconds=145))
        def release(self,activation_id): restored.append('released')
    import experiments.faults.lifecycle as lifecycle
    import sys, types
    fake_datasets = types.ModuleType('datasets')
    fake_datasets.__path__ = []
    fake_window = types.ModuleType('datasets.phase19c2b_exact_windows')
    fake_window.select_exact_window = lambda **k: None
    fake_window.build_observable_features = lambda **k: None
    fake_runner = types.ModuleType('experiments.runner')
    async def fail(*a,**k): raise RuntimeError('synthetic child failure')
    fake_runner.PlannedRequest = lambda **kwargs: kwargs
    fake_runner.execute_plan = fail
    fake_runner.collect_run_events = lambda run_id: []
    monkeypatch.setitem(sys.modules, 'datasets', fake_datasets)
    monkeypatch.setitem(sys.modules, 'datasets.phase19c2b_exact_windows', fake_window)
    monkeypatch.setitem(sys.modules, 'experiments.runner', fake_runner)
    monkeypatch.setattr(lifecycle,'FaultStateStore',FakeStore)
    with pytest.raises(RuntimeError,match='synthetic child failure'):
        asyncio.run(p.run_pilot('DATABASE_UNAVAILABLE','postgresql',1.5,'http://127.0.0.1:18110'))
    assert restored == ['up','released']


def test_supervisor_refuses_unacknowledged_postgres_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(k,'ROOT',tmp_path)
    monkeypatch.setattr(k,'verify_blocked_project',lambda root:{'proposal_sha256':'fixture','runs':[]})
    monkeypatch.setattr(k,'run_diagnostic',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('Docker would be contacted')))
    result=k.main(['--pilot','--kind','database-unavailable','--target','postgresql',
                   '--rps','1.5','--execute-pilot','--attempt-id','manual-db-test-01',
                   '--acknowledge-diagnostic-only'])
    assert result == 2


def test_supervisor_db_outage_flag_forbidden_on_high_load(monkeypatch, tmp_path):
    monkeypatch.setattr(k,'ROOT',tmp_path)
    monkeypatch.setattr(k,'verify_blocked_project',lambda root:{'proposal_sha256':'fixture','runs':[]})
    monkeypatch.setattr(k,'run_diagnostic',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('Docker would be contacted')))
    result=k.main(['--pilot','--kind','high-load','--target','gateway',
                   '--rps','8.0','--execute-pilot','--attempt-id','manual-load-test-01',
                   '--acknowledge-diagnostic-only','--acknowledge-local-db-outage'])
    assert result == 2
