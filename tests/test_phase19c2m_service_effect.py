"""Offline-only tests: no Docker, HTTP, live faults, model access, or writes to repo."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.phase19c2m_service_effect_pilot import (
    ALLOWED, INTENSITY, RATE, TARGET_PATHS, RESERVED_BUT_NOT_SAFE,
    allowed_configuration, execute_fault_phase, observed_impact, main,
)


def windowed(fault='DATABASE_HIGH_LATENCY', target='payment', n=90, matched=90):
    now = datetime.now(UTC)
    window = SimpleNamespace(started_at=now, ended_at=now+timedelta(seconds=60))
    requests, events = [], []
    for i in range(n):
        rid = f'run-request-{i:05d}'
        at = (now+timedelta(seconds=1+(i % 58))).isoformat()
        good = i < matched
        requests.append(dict(request_id=rid, trace_id='trace-'+rid, started_at=at,
                             latency_ms=850 if good else 90,
                             status_code=(500 if fault == 'SERVICE_UNAVAILABLE' and good else 200),
                             transport_error=None))
        events.append(dict(request_id=rid, trace_id='trace-'+rid, timestamp=at,
                           service=target, event_type='http_request_completed',
                           path=TARGET_PATHS[target]+'/execute',
                           latency_ms=800 if good else 90,
                           status_code=(503 if fault == 'SERVICE_UNAVAILABLE' and good else 200)))
    return window, requests, events


@pytest.mark.parametrize('fault,target', [
    ('DATABASE_HIGH_LATENCY', 'payment'),
    ('DATABASE_HIGH_LATENCY', 'transaction'),
    ('DATABASE_HIGH_LATENCY', 'ledger'),
    ('SERVICE_UNAVAILABLE','payment'),
    ('SERVICE_UNAVAILABLE','ledger'),
])
def test_approved_profiles(fault, target):
    allowed_configuration(fault, target, RATE)


@pytest.mark.parametrize('fault,target', [
    ('DATABASE_UNAVAILABLE','postgresql'),
    ('HIGH_LOAD','gateway'),
    ('CONNECTION_POOL_EXHAUSTION','payment'),
    ('DATABASE_LOCK_CONTENTION','payment'),
    ('SERVICE_UNAVAILABLE','account'),
    ('SERVICE_UNAVAILABLE','transaction'),
    ('DATABASE_HIGH_LATENCY','authentication'),
    ('DOWNSTREAM_TIMEOUT','payment->ledger'),
])
def test_unsafe_configs_blocked(fault,target):
    with pytest.raises(ValueError):
        allowed_configuration(fault,target,RATE)


@pytest.mark.parametrize('rate', [0.0,1.2,2.0,2.5,20.0,float('nan'),float('inf')])
def test_unapproved_rate_blocked(rate):
    with pytest.raises(ValueError):
        allowed_configuration('DATABASE_HIGH_LATENCY','payment',rate)


def test_database_latency_proven():
    w,r,e=windowed()
    result=observed_impact(w,r,e,'DATABASE_HIGH_LATENCY','payment')
    assert result['correlated_slow_client_and_target']==90
    assert result['latency_threshold_ms']==600


def test_latency_small_slow_fraction_rejected():
    w,r,e=windowed(matched=30)
    with pytest.raises(RuntimeError,match='DATABASE_DELAY_NOT_PROVEN'):
        observed_impact(w,r,e,'DATABASE_HIGH_LATENCY','payment')


def test_latency_requires_target_service_correlation():
    w,r,e=windowed()
    for row in e: row['service']='account'
    with pytest.raises(RuntimeError):
        observed_impact(w,r,e,'DATABASE_HIGH_LATENCY','payment')


def test_latency_requires_trace_match():
    w,r,e=windowed()
    for row in e: row['trace_id']='wrong'
    with pytest.raises(RuntimeError):
        observed_impact(w,r,e,'DATABASE_HIGH_LATENCY','payment')


def test_latency_requires_client_impact():
    w,r,e=windowed()
    for row in r: row['latency_ms']=80
    with pytest.raises(RuntimeError):
        observed_impact(w,r,e,'DATABASE_HIGH_LATENCY','payment')


def test_service_503_proven():
    w,r,e=windowed('SERVICE_UNAVAILABLE', 'ledger')
    result=observed_impact(w,r,e,'SERVICE_UNAVAILABLE','ledger')
    assert result['correlated_503_with_client_impact']==90


def test_service_requires_503_not_500():
    w,r,e=windowed('SERVICE_UNAVAILABLE')
    for row in e:row['status_code']=500
    with pytest.raises(RuntimeError,match='SERVICE_503_NOT_PROVEN'):
        observed_impact(w,r,e,'SERVICE_UNAVAILABLE','payment')


def test_service_requires_client_propagation():
    w,r,e=windowed('SERVICE_UNAVAILABLE')
    for row in r:row['status_code']=200
    with pytest.raises(RuntimeError):
        observed_impact(w,r,e,'SERVICE_UNAVAILABLE','payment')


def test_service_requires_true_path():
    w,r,e=windowed('SERVICE_UNAVAILABLE')
    for row in e:row['path']='/health/ready'
    with pytest.raises(RuntimeError):
        observed_impact(w,r,e,'SERVICE_UNAVAILABLE','payment')


def test_service_requires_majority():
    w,r,e=windowed('SERVICE_UNAVAILABLE', matched=35)
    with pytest.raises(RuntimeError):
        observed_impact(w,r,e,'SERVICE_UNAVAILABLE','payment')


def test_window_minimum():
    w,r,e=windowed(n=59)
    with pytest.raises(RuntimeError,match='INSUFFICIENT'):
        observed_impact(w,r,e,'DATABASE_HIGH_LATENCY','payment')


class FakeStore:
    def __init__(self):
        self.activation = SimpleNamespace(activation_id='a', expires_at=datetime.now(UTC)+timedelta(seconds=105))
        self.release_count=0
    def acquire(self,**kw):
        assert kw['fault_id']=='DATABASE_HIGH_LATENCY'
        assert kw['duration_seconds']==105
        return self.activation
    def active(self):return self.activation
    def release(self,id):
        assert id=='a'
        self.release_count += 1


def test_lease_cleanup_on_exception():
    store=FakeStore()
    async def bad(*args):raise ValueError('simulated failure')
    with pytest.raises(ValueError,match='simulated'):
        asyncio.run(execute_fault_phase(store,'run','DATABASE_HIGH_LATENCY','payment', [], 'local',bad))
    assert store.release_count==1


def test_lease_cleanup_on_cancelled():
    store=FakeStore()
    async def cancelled(*args):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute_fault_phase(store,'run','DATABASE_HIGH_LATENCY','payment', [], 'local',cancelled))
    assert store.release_count==1


def test_lease_cleanup_on_success():
    store=FakeStore()
    async def ok(*args):return [{'request_id':'x'}]
    value=asyncio.run(execute_fault_phase(store,'run','DATABASE_HIGH_LATENCY','payment', [], 'local',ok))
    assert value[0]==[{'request_id':'x'}]
    assert store.release_count==1


def test_lease_expiry_failure_cleaned():
    store=FakeStore()
    store.activation.expires_at=datetime.now(UTC)-timedelta(seconds=1)
    async def ok(*args):return []
    with pytest.raises(RuntimeError,match='expired'):
        asyncio.run(execute_fault_phase(store,'run','DATABASE_HIGH_LATENCY','payment', [], 'local',ok))
    assert store.release_count==1


def test_dry_run_no_external_side_effect(monkeypatch,capsys):
    monkeypatch.setattr('scripts.phase19c2m_service_effect_pilot.blocked_plan_gate',lambda:None)
    assert main(['--dry-run','--fault','DATABASE_HIGH_LATENCY','--target','payment'])==0
    assert '240 plan rows remain blocked' in capsys.readouterr().out


def test_live_requires_ack_before_run(monkeypatch):
    monkeypatch.setattr('scripts.phase19c2m_service_effect_pilot.blocked_plan_gate',lambda:None)
    with pytest.raises(ValueError,match='acknowledge'):
        main(['--execute','--fault','DATABASE_HIGH_LATENCY','--target','payment'])


def test_cli_rejects_infrastructure_without_touching_system():
    with pytest.raises(ValueError):
        main(['--dry-run','--fault','DATABASE_UNAVAILABLE','--target','postgresql'])


def test_no_family_implicitly_admitted():
    assert len(ALLOWED)==2
    assert 'DATABASE_UNAVAILABLE' in RESERVED_BUT_NOT_SAFE
    assert INTENSITY=='medium'

@pytest.mark.parametrize('kind,target,fault',[('database-latency','payment','DATABASE_HIGH_LATENCY'),
    ('database-latency','transaction','DATABASE_HIGH_LATENCY'),
    ('database-latency','ledger','DATABASE_HIGH_LATENCY'),
    ('service-unavailable','payment','SERVICE_UNAVAILABLE'),
    ('service-unavailable','ledger','SERVICE_UNAVAILABLE')])
def test_unified_controller_adapter(kind,target,fault):
    from scripts import phase19c2k_collection_controller as k
    adapter=k.choose_adapter(kind,target,1.5)
    assert adapter.fault==fault
    assert adapter.prefix=='v2effect-'
    assert adapter.relative_dir=='data/datasets/v2/pilot-fault'
    cmd=adapter.command(execute=True)
    assert cmd[1:3]==['-m','scripts.phase19c2m_service_effect_pilot']
    assert cmd[cmd.index('--fault')+1]==fault
    assert cmd[cmd.index('--target')+1]==target
    assert '--acknowledge-diagnostic-only' in cmd
    dry=adapter.command(execute=False)
    assert '--execute' not in dry
    assert '--acknowledge-diagnostic-only' not in dry


@pytest.mark.parametrize('kind,target,rps', [('database-latency','account',1.5),
    ('service-unavailable','account',1.5),('database-latency','payment',2.0)])
def test_controller_refuses_unapproved_effect(kind,target,rps):
    from scripts import phase19c2k_collection_controller as k
    with pytest.raises(ValueError):
        k.choose_adapter(kind,target,rps)


def test_controller_effect_dry_run_readonly(monkeypatch,tmp_path,capsys):
    from scripts import phase19c2k_collection_controller as k
    monkeypatch.setattr(k,'ROOT',tmp_path)
    monkeypatch.setattr(k,'verify_blocked_project',lambda root:{'proposal_sha256':'sealed-test'})
    assert k.main(['--pilot','--kind','database-latency','--target','payment',
                   '--rps','1.5','--dry-run'])==0
    assert 'NEW UUID PILOT ONLY' in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_auth_adapter_reuse_no_new_auth_collector():
    from scripts import phase19c2k_collection_controller as k
    adapter=k.choose_adapter('auth-rejection','authentication',1.5)
    assert adapter.module=='scripts.phase19c2l_auth_pilot'
    assert adapter.fault=='AUTHENTICATION_FAILURE_BURST'
    assert adapter.prefix=='v2auth-'
    assert adapter.command(execute=True)[1:3]==['-m','scripts.phase19c2l_auth_pilot']
    assert '--acknowledge-diagnostic-only' not in adapter.command(execute=True)


@pytest.mark.parametrize('target,rps', [('payment',1.5), ('authentication',2.0), (None,1.5)])
def test_auth_adapter_unapproved_rejected(target,rps):
    from scripts import phase19c2k_collection_controller as k
    with pytest.raises(ValueError): k.choose_adapter('auth-rejection',target,rps)
