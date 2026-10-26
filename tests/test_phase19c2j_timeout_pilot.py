"""Offline-only Phase 19C.2J tests, no Docker, HTTP, file writes or fault activation."""
from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
from scripts.phase19c2j_timeout_pilot import (
    FAULT_ID, INTENSITY, TARGETS, timeout_impact, execute_bounded_timeout, main,
    blocked_plan_gate,
)

T = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
W = SimpleNamespace(started_at=T, ended_at=T+timedelta(seconds=60))
RUN = 'v2timeout-0123456789abcdef'


def req(i, status=503, trace=None, when=None):
    return {'run_id':RUN,'request_id':f'{RUN}-request-{i:05d}',
            'trace_id':trace or f'trace-{i}',
            'started_at':(when or T+timedelta(seconds=i/2)).isoformat(),
            'status_code':status,'transport_error':None}


def event(row, edge, error='ReadTimeout', source=None, downstream=None, path=None, when=None):
    a,b=edge.split('->')
    return {'run_id':RUN,'request_id':row['request_id'],'trace_id':row['trace_id'],
            'timestamp':(when or datetime.fromisoformat(row['started_at'])+timedelta(seconds=1)).isoformat(),
            'service':source or a, 'downstream_service':downstream or b,
            'event_type':'downstream_request_failed','error_type':error,
            'path':path or ('/internal/v1/payments' if a=='transaction' else '/internal/v1/ledger/entries')}


@pytest.mark.parametrize('edge', TARGETS)
def test_both_observed_targets_produce_evidence(edge):
    requests=[req(i) for i in range(1,101)]
    events=[event(row,edge) for row in requests]
    r=timeout_impact(W,requests,events,edge)
    assert r['edge']==edge and r['correlated_source_target_timeouts']>=80
    assert r['timeouts_with_affected_clients']>=80


@pytest.mark.parametrize('edge', TARGETS)
def test_wrong_source_target_does_not_count(edge):
    r=[req(i) for i in range(1,101)]
    e=[event(row,edge,source='gateway') for row in r]
    with pytest.raises(RuntimeError,match='INVALID_TIMEOUT_EXPOSURE'):
        timeout_impact(W,r,e,edge)


def test_wrong_trace_does_not_count():
    edge=TARGETS[0]
    r=[req(i) for i in range(1,101)]
    e=[event(row,edge) for row in r]
    for x in e:x['trace_id']='wrong'
    with pytest.raises(RuntimeError,match='INVALID_TIMEOUT_EXPOSURE'):
        timeout_impact(W,r,e,edge)


def test_non_timeout_error_does_not_count():
    edge=TARGETS[1];r=[req(i) for i in range(1,101)]
    with pytest.raises(RuntimeError):timeout_impact(W,r,[event(x,edge,error='ConnectError') for x in r],edge)


def test_fault_occurred_outside_window_does_not_count():
    edge=TARGETS[0];r=[req(i) for i in range(1,101)]
    when=W.ended_at+timedelta(seconds=1)
    with pytest.raises(RuntimeError):timeout_impact(W,r,[event(x,edge,when=when) for x in r],edge)


def test_healthy_clients_do_not_count_as_affected():
    edge=TARGETS[0];r=[req(i,status=200) for i in range(1,101)]
    with pytest.raises(RuntimeError):timeout_impact(W,r,[event(x,edge) for x in r],edge)


def test_wrong_endpoint_does_not_count():
    edge=TARGETS[1];r=[req(i) for i in range(1,101)]
    with pytest.raises(RuntimeError):timeout_impact(W,r,[event(x,edge,path='/internal/v1/payments') for x in r],edge)


def test_no_unknown_edge():
    with pytest.raises(ValueError):timeout_impact(W,[],[], 'payment->account')


def test_too_few_clients_rejected():
    edge=TARGETS[0];r=[req(i) for i in range(1,30)]
    with pytest.raises(RuntimeError):timeout_impact(W,r,[event(x,edge) for x in r],edge)


class FakeStore:
    def __init__(self):
        self.activation=SimpleNamespace(activation_id='f'*32,expires_at=datetime.now(UTC)+timedelta(seconds=105))
        self.acquired=[];self.released=[]
    def acquire(self,**kw):self.acquired.append(kw);return self.activation
    def active(self):return self.activation
    def release(self,identity):self.released.append(identity)


@pytest.mark.parametrize('target',TARGETS)
def test_guaranteed_release_success(target):
    store=FakeStore()
    async def run(*args):return [{'ok':True}]
    result=asyncio.run(execute_bounded_timeout(store,RUN,target,[], 'http://127.0.0.1:18110',run))
    assert result[0]==[{'ok':True}]
    assert len(store.released)==1
    assert store.acquired[0]['fault_id']==FAULT_ID
    assert store.acquired[0]['intensity']==INTENSITY
    assert store.acquired[0]['target']==target


@pytest.mark.parametrize('error', [RuntimeError('mock failed'), asyncio.CancelledError()])
def test_release_on_failure(error):
    store=FakeStore()
    async def run(*args):raise error
    with pytest.raises(type(error)):
        asyncio.run(execute_bounded_timeout(store,RUN,TARGETS[0],[], 'http://127.0.0.1:18110',run))
    assert len(store.released)==1


def test_refuse_invalid_target_before_lease():
    store=FakeStore()
    async def run(*args):raise AssertionError('Should never be called')
    with pytest.raises(ValueError):
        asyncio.run(execute_bounded_timeout(store,RUN,'payment->account',[], 'http://127.0.0.1:18110',run))
    assert store.acquired==[]


def test_bad_lease_ownership_released():
    store=FakeStore();store.active=lambda:None
    async def run(*args):raise AssertionError('No workload')
    with pytest.raises(RuntimeError,match='ownership'):
        asyncio.run(execute_bounded_timeout(store,RUN,TARGETS[0],[], 'http://127.0.0.1:18110',run))
    assert len(store.released)==1


def test_dry_run_no_network(monkeypatch,capsys):
    import scripts.phase19c2j_timeout_pilot as m
    monkeypatch.setattr(m,'blocked_plan_gate',lambda:None)
    monkeypatch.setattr(m,'require_clean_local_stack',lambda:pytest.fail('Docker accessed'))
    assert main(['--dry-run','--target',TARGETS[0],'--rps','1.5'])==0
    assert 'no docker' in capsys.readouterr().out.lower()


def test_disallow_unverified_rate(monkeypatch):
    import scripts.phase19c2j_timeout_pilot as m
    monkeypatch.setattr(m,'blocked_plan_gate',lambda:None)
    with pytest.raises(ValueError,match='previously tested'):
        main(['--dry-run','--target',TARGETS[0],'--rps','1.8'])


def test_disallow_external_gateway(monkeypatch):
    import scripts.phase19c2j_timeout_pilot as m
    monkeypatch.setattr(m,'blocked_plan_gate',lambda:None)
    with pytest.raises(ValueError):main(['--dry-run','--target',TARGETS[0], '--gateway-url','https://example.com'])


def test_reuse_blocked_plan_gate(monkeypatch):
    import scripts.phase19c2j_timeout_pilot as m
    monkeypatch.setattr(m,'strict_json',lambda _: {})
    monkeypatch.setattr(m,'verify_blocked_matrix',lambda p,q:{'blocked':239})
    with pytest.raises(RuntimeError,match='blocked'):blocked_plan_gate()
