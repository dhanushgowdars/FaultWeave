"""Offline regression, isolation and anti-false-positive checks for resource pilots."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import phase19c2n_resource_pilot as p
from scripts import phase19c2k_collection_controller as k


def _evidence(kind='CONNECTION_POOL_EXHAUSTION', pressure=15, waiters=0):
    start=datetime(2026,10,11,0,0,0,tzinfo=UTC)
    window=SimpleNamespace(started_at=start, ended_at=start+timedelta(seconds=60))
    requests=[]
    events=[]
    for i in range(90):
        rid=f'diagnostic-request-{i:05d}'
        instant=start+timedelta(seconds=0.5+i*.62)
        status=500 if i < 35 else 200
        requests.append({'request_id':rid,'trace_id':'trace-'+rid,
                         'started_at':instant.isoformat(), 'status_code':status,
                         'transport_error':None})
        events.append({'request_id':rid, 'trace_id':'trace-'+rid,
                       'timestamp':instant.isoformat(), 'service':'gateway',
                       'event_type':'http_request_completed', 'status_code':status, 'success':status==200})
    samples=[]
    for i in range(25):
        t=start+timedelta(seconds=4+2*i)
        samples.append({'timestamp':t.isoformat(), 'total_connections':14+pressure,
                        'advisory_waiters':waiters,'advisory_holders':1})
    baseline={'baseline_connections':14,'baseline_waiters':0}
    return window,requests,events,samples,kind,baseline


@pytest.mark.parametrize('fault',p.FAULTS)
def test_supported_exact_payment_diagnostic(fault):
    p.configuration(fault, 'payment', 1.5)


@pytest.mark.parametrize('fault,target,rps',[('HIGH_LOAD','payment',1.5),
    ('DATABASE_UNAVAILABLE','payment',1.5),('CONNECTION_POOL_EXHAUSTION','ledger',1.5),
    ('DATABASE_LOCK_CONTENTION','transaction',1.5),('DATABASE_LOCK_CONTENTION','payment',2.0),
    ('CONNECTION_POOL_EXHAUSTION','payment',float('nan')),
    ('CONNECTION_POOL_EXHAUSTION','payment',True)])
def test_unsafe_configs_rejected(fault,target,rps):
    with pytest.raises(ValueError):p.configuration(fault,target,rps)


def test_physical_pool_pressure_and_correlated_client_evidence():
    args=_evidence()
    proof=p.effect_gate(*args)
    assert proof['peak_connections']==29
    assert proof['correlated_client_failures']==35


def test_pool_pressure_not_proven_by_clients_alone():
    args=_evidence(pressure=2)
    with pytest.raises(RuntimeError,match='connection pressure'):p.effect_gate(*args)


def test_pg_lock_waiters_and_correlated_client_evidence():
    args=_evidence(kind='DATABASE_LOCK_CONTENTION',pressure=1,waiters=3)
    proof=p.effect_gate(*args)
    assert proof['peak_waiters']==3


def test_lock_fault_not_proven_by_client_errors_alone():
    args=_evidence(kind='DATABASE_LOCK_CONTENTION',pressure=1,waiters=0)
    with pytest.raises(RuntimeError,match='advisory lock waiters'):p.effect_gate(*args)


def test_no_effect_when_clients_succeed():
    args=list(_evidence())
    for req in args[1]:req['status_code']=200
    with pytest.raises(RuntimeError,match='client failures'):p.effect_gate(*args)


def test_not_enough_physical_samples_fails():
    args=list(_evidence())
    args[3]=args[3][:1]
    with pytest.raises(RuntimeError,match='physical samples'):p.effect_gate(*args)


def test_no_effect_without_matching_request_trace():
    args=list(_evidence())
    for evt in args[2]:evt['trace_id']='wrong'
    with pytest.raises(RuntimeError,match='correlated client failures'):p.effect_gate(*args)


def test_snapshots_parse_three_nonnegative_columns(monkeypatch):
    def fake(*args):
        if 'printenv' in args: return 'faultweave'
        return '23,4,2'
    monkeypatch.setattr(p,'command',fake)
    assert p.database_snapshot()=={'total_connections':23,'advisory_waiters':4,'advisory_holders':2}


@pytest.mark.parametrize('bad',['23,4','23,4,2,1','23,-1,2','abc,2,1'])
def test_malformed_snapshot_is_rejected(monkeypatch,bad):
    def fake(*args):
        if 'printenv' in args:return 'faultweave'
        return bad
    monkeypatch.setattr(p,'command',fake)
    with pytest.raises(RuntimeError):p.database_snapshot()


def test_preflight_detects_inadequate_headroom(monkeypatch,tmp_path):
    root=tmp_path
    target=root/'shared/faultweave_common/fault_injection.py'
    target.parent.mkdir(parents=True)
    target.write_text('fixture')
    digest=hashlib.sha256(b'fixture').hexdigest()
    monkeypatch.setattr(p,'ROOT',root)
    monkeypatch.setattr(p,'blocked_plan_gate',lambda:None)
    monkeypatch.setattr(p,'require_clean_local_stack',lambda:None)
    monkeypatch.setattr(p,'database_snapshot',lambda:{'total_connections':75,'advisory_waiters':0})
    monkeypatch.setattr(p,'verify_running_payment_worker',lambda:None)
    def fake(*args):
        if args[:2]==('config','--format'):
            return json.dumps({'services':{'payment-service':{'image':'faultweave-payment-service'}}})
        if 'printenv' in args:return 'faultweave'
        if 'SHOW max_connections;' in args:return '100'
        if 'SHOW superuser_reserved_connections;' in args:return '3'
        if 'SHOW reserved_connections;' in args:return '0'
        return digest
    monkeypatch.setattr(p,'command',fake)
    with pytest.raises(RuntimeError,match='insufficient connection budget'):
        p.ensure_live_preflight('CONNECTION_POOL_EXHAUSTION')


def test_new_supervisor_adapters_are_only_payment_1_5_rps():
    for kind,fault in [('connection-pool','CONNECTION_POOL_EXHAUSTION'),
                       ('database-lock','DATABASE_LOCK_CONTENTION')]:
        adapter=k.choose_adapter(kind,'payment',1.5)
        assert adapter.fault==fault
        assert adapter.prefix=='v2resource-'
        assert adapter.command(execute=False)[-4:] == ['--fault', fault, '--target', 'payment']
        with pytest.raises(ValueError):k.choose_adapter(kind,'ledger',1.5)
        with pytest.raises(ValueError):k.choose_adapter(kind,'payment',2.0)


def test_new_supervisor_never_executes_blocked_plan():
    from scripts.phase19c2k_collection_controller import planned_row
    row={'run_id':'v2-normal-low-01','execution_allowed':False}
    assert planned_row({'runs':[row]},row['run_id'])['execution_allowed'] is False


def test_dry_run_offline_does_not_call_docker(monkeypatch,capsys):
    monkeypatch.setattr(p,'blocked_plan_gate',lambda:None)
    monkeypatch.setattr(p,'command',lambda *args: (_ for _ in ()).throw(AssertionError('Docker called')))
    assert p.main(['--dry-run','--fault','CONNECTION_POOL_EXHAUSTION','--rps','1.5'])==0
    assert 'NO Docker' in capsys.readouterr().out


def test_preflight_rejects_running_multiworker(monkeypatch):
    monkeypatch.setattr(p,'command',lambda *a: '0123456789abcdef')
    class Result:
        returncode=0
        stdout='["uvicorn","app.main:app","--workers","4"]'
    monkeypatch.setattr(p.subprocess,'run',lambda *a,**kw: Result())
    with pytest.raises(RuntimeError,match='unapproved worker count'):
        p.verify_running_payment_worker()


def test_snapshot_with_identity_avoids_extra_env_exec(monkeypatch):
    calls=[]
    def fake(*args):
        calls.append(args)
        assert 'printenv' not in args
        return '28,1,3'
    monkeypatch.setattr(p,'command',fake)
    assert p.database_snapshot('faultweave','faultweave')['total_connections']==28
    assert len(calls)==1
