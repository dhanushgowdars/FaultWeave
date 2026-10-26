"""Offline tests for Phase 19C.2L safety and authentication rejection evidence."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import phase19c2l_auth_pilot as pilot
from scripts import phase19c2l_auth_audit as auditor

START = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
RID = 'v2auth-0123456789abcdef'


def window():
    return SimpleNamespace(started_at=START, ended_at=START+timedelta(seconds=60))


def observations(n=60, code=401):
    clients, events = [], []
    for i in range(1, n+1):
        id = f'{RID}-request-{i:05d}'
        now = START+timedelta(seconds=i)
        clients.append(dict(run_id=RID, request_id=id, trace_id=f'trace-{i}',
                            started_at=now.isoformat(), scenario='invalid_login',
                            expected_outcome=(code == 401), status_code=code, transport_error=None))
        events.append(dict(run_id=RID, request_id=id, trace_id=f'trace-{i}',
                           timestamp=now.isoformat(), event_type='http_request_completed',
                           service='authentication', status_code=401))
    return clients, events


def test_auth_plan_only_invalid_login():
    plan = pilot.auth_plan(RID)
    assert len(plan) == round(pilot.DURATION_SECONDS * 1.5)
    assert {p.scenario for p in plan} == {'invalid_login'}
    assert [p.sequence for p in plan] == list(range(1,len(plan)+1))


def test_auth_plan_does_not_mutate_valid_template():
    assert pilot.new_plan(RID,1.5)[0].scenario == 'valid'
    assert pilot.auth_plan(RID)[0].scenario == 'invalid_login'


def test_auth_rows_preserve_outgoing_provenance():
    row = dict(request_id=f'{RID}-request-00001', trace_id='t1', started_at=START.isoformat(),
               scenario='invalid_login', status_code=401, expected_outcome=True, transport_error=None)
    result = pilot.auth_rows([row],RID)
    assert result[0]['run_id'] == RID and result[0]['scenario']=='invalid_login'
    assert 'password' not in result[0]


@pytest.mark.parametrize('change',[
    {'scenario':'valid'}, {'trace_id':''}, {'request_id':'wrong'}, {'started_at':None},
])
def test_auth_rows_reject_invalid_provenance(change):
    row = dict(request_id=f'{RID}-request-00001', trace_id='t1', started_at=START.isoformat(),
               scenario='invalid_login', status_code=401, expected_outcome=True, transport_error=None)
    row.update(change)
    with pytest.raises(ValueError):pilot.auth_rows([row],RID)


def test_auth_rows_reject_duplicates():
    row = dict(request_id=f'{RID}-request-00001', trace_id='t1', started_at=START.isoformat(),
               scenario='invalid_login', status_code=401, expected_outcome=True, transport_error=None)
    with pytest.raises(ValueError):pilot.auth_rows([row,row],RID)


def test_auth_impact_accepts_correlated_auth_rejections():
    clients,events=observations()
    impact=pilot.auth_impact(window(),clients,events)
    assert impact['client_401_rejections']==60
    assert impact['correlated_authentication_401_requests']==60
    assert impact['high_rate_burst_proven'] is False


def test_auth_impact_rejects_success_responses():
    clients,events=observations(code=200)
    with pytest.raises(RuntimeError,match='AUTH_REJECTION_NOT_PROVEN'):
        pilot.auth_impact(window(),clients,events)


def test_auth_impact_rejects_missing_server_events():
    clients,events=observations()
    with pytest.raises(RuntimeError,match='AUTH_SERVICE_LOGS_NOT_PROVEN'):
        pilot.auth_impact(window(),clients,events[:10])


def test_auth_impact_rejects_unrelated_server_trace():
    clients,events=observations()
    for event in events:event['trace_id']='different'
    with pytest.raises(RuntimeError,match='AUTH_SERVICE_LOGS_NOT_PROVEN'):
        pilot.auth_impact(window(),clients,events)


def test_auth_impact_rejects_gateway_only_events():
    clients,events=observations()
    for event in events:event['service']='gateway'
    with pytest.raises(RuntimeError,match='AUTH_SERVICE_LOGS_NOT_PROVEN'):
        pilot.auth_impact(window(),clients,events)


def test_auth_impact_rejects_outside_window_events():
    clients,events=observations()
    for event in events:event['timestamp']=(START-timedelta(seconds=10)).isoformat()
    with pytest.raises(RuntimeError,match='AUTH_SERVICE_LOGS_NOT_PROVEN'):
        pilot.auth_impact(window(),clients,events)


def test_auth_impact_rejects_insufficient_coverage():
    clients,events=observations(35)
    with pytest.raises(RuntimeError,match='AUTH_REJECTION_NOT_PROVEN'):
        pilot.auth_impact(window(),clients,events)


def test_auth_impact_rejects_high_client_failure_rate():
    clients,events=observations()
    for row in clients[:7]:row['status_code']=500
    with pytest.raises(RuntimeError,match='AUTH_REJECTION_NOT_PROVEN'):
        pilot.auth_impact(window(),clients,events)


def test_cli_dry_run_only_previous_healthy_rate(monkeypatch,capsys):
    monkeypatch.setattr(pilot,'blocked_plan_gate',lambda:None)
    assert pilot.main(['--dry-run','--rps','1.5'])==0
    assert '240 runs BLOCKED' in capsys.readouterr().out
    for rps in ('2','20','40','60','nan','inf'):
        with pytest.raises(ValueError):pilot.main(['--dry-run','--rps',rps])


def test_cli_rejects_nonlocal_gateway(monkeypatch):
    monkeypatch.setattr(pilot,'blocked_plan_gate',lambda:None)
    with pytest.raises(ValueError):
        pilot.main(['--dry-run','--gateway-url','https://external.example'])


class FakeActivation:
    def __init__(self):
        self.activation_id='abc'
        self.expires_at=START+timedelta(seconds=300)
        self.activated_at=START


class FakeStore:
    def __init__(self):self.releases=[];self.activation=FakeActivation()
    def acquire(self,**kwargs):return self.activation
    def active(self):return self.activation
    def release(self,activation_id):self.releases.append(activation_id)


def test_lease_released_on_executor_failure():
    store=FakeStore()
    async def fail(*args):raise RuntimeError('simulated failure')
    with pytest.raises(RuntimeError,match='simulated failure'):
        asyncio.run(pilot.execute_auth_phase(store,RID,[],'http://127.0.0.1:18110',fail))
    assert store.releases==['abc']


def test_lease_released_on_cancelled_workload():
    store=FakeStore()
    async def fail(*args):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pilot.execute_auth_phase(store,RID,[],'http://127.0.0.1:18110',fail))
    assert store.releases==['abc']


def test_audit_rejects_inaccessible_folder(tmp_path):
    with pytest.raises((ValueError,FileNotFoundError)):
        auditor.audit(tmp_path, tmp_path/'does-not-exist')


def test_audit_rejects_rogue_directory(tmp_path):
    (tmp_path/'wrong').mkdir()
    with pytest.raises((ValueError,FileNotFoundError)):
        auditor.audit(tmp_path,tmp_path/'wrong')


def test_persist_writes_unlabeled_create_only(monkeypatch,tmp_path):
    import sys,types
    mock=types.ModuleType('datasets.final_features')
    mock.FEATURE_NAMES=tuple(f'f{i}' for i in range(65))
    monkeypatch.setitem(sys.modules,'datasets.final_features',mock)
    monkeypatch.setattr(pilot,'OUT_DIR',tmp_path)
    vector={f'f{i}':float(i) for i in range(65)}
    out=pilot.persist(RID,[{'test':1}],[{'test':2}],[{'test':3}],window(),vector,{'fault_id':pilot.FAULT_ID},{'test':4})
    assert out.name==RID
    man=json.loads((out/'manifest.json').read_text())
    candidate=json.loads((out/'candidate_feature.json').read_text())
    assert man['training_eligible'] is False and candidate['label'] is None
    assert set(man['artifacts'])==set(auditor.FILES)
    with pytest.raises(RuntimeError,match='create-only'):
        pilot.persist(RID,[],[],[],window(),vector,{}, {})
