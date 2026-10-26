"""Guard the resource-effect gate against the missing-outcome schema bug."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from scripts import phase19c2n_resource_pilot as p


def evidence():
    start = datetime(2026,10,11,tzinfo=UTC)
    win = SimpleNamespace(started_at=start, ended_at=start+timedelta(seconds=60))
    requests=[]; events=[]
    for i in range(90):
        when = (start+timedelta(seconds=1+i*.6)).isoformat()
        rid=f'test-request-{i}'; trace=f'trace-{i}'
        requests.append(dict(request_id=rid,trace_id=trace,started_at=when,
                             status_code=503,transport_error=None))
        events.append(dict(request_id=rid,trace_id=trace,timestamp=when,
                           service='gateway',event_type='http_request_completed',
                           status_code=503,success=False))
    samples = [dict(timestamp=(start+timedelta(seconds=1+i*2)).isoformat(),
                    total_connections=42, advisory_waiters=0, advisory_holders=0)
               for i in range(25)]
    baseline={'baseline_connections':23,'baseline_waiters':0}
    return win, requests, events, samples, 'CONNECTION_POOL_EXHAUSTION', baseline


def test_saved_schema_without_outcome_passes_when_proven():
    args=evidence()
    assert all('outcome' not in ev for ev in args[2])
    result=p.effect_gate(*args)
    assert result['correlated_client_failures']==90
    assert result['peak_connections']==42


def test_explicit_http_error_not_just_success_false():
    for changes in [
        dict(status_code=200), dict(status_code=None), dict(status_code=True),
        dict(event_type='authentication_failed'), dict(event_type='transaction_flow_started'),
        dict(success=True), dict(success=None), dict(event_type=None),
    ]:
        ev=dict(event_type='http_request_completed',status_code=503,success=False)
        ev.update(changes)
        assert not p.is_observable_service_failure(ev)


def test_explicit_failure_event_without_status():
    for event_type in ('downstream_request_failed','http_request_failed','transaction_flow_failed'):
        assert p.is_observable_service_failure(dict(event_type=event_type,success=False))
    assert not p.is_observable_service_failure(dict(event_type='downstream_request_completed',success=False))
    assert not p.is_observable_service_failure(dict(event_type='downstream_request_failed',success=True))


@pytest.mark.parametrize('corrupt', ['trace','success','time','service','status','event'])
def test_no_false_correlation_when_metadata_invalid(corrupt):
    args=list(evidence())
    for e in args[2]:
        if corrupt=='trace': e['trace_id']='unrelated'
        elif corrupt=='success': e['success']=True
        elif corrupt=='time': e['timestamp']=(args[0].ended_at+timedelta(seconds=1)).isoformat()
        elif corrupt=='service': e['service']='unrelated-service'
        elif corrupt=='status': e['status_code']=200
        else: e['event_type']='authentication_succeeded'
    with pytest.raises(RuntimeError, match='correlated_client_failures=0/10'):
        p.effect_gate(*args)


def test_physical_pressure_still_mandatory():
    args=list(evidence())
    for item in args[3]: item['total_connections']=24
    with pytest.raises(RuntimeError, match='connection pressure'):
        p.effect_gate(*args)


def test_advisory_waiters_still_mandatory_for_lock():
    args=list(evidence()); args[4]='DATABASE_LOCK_CONTENTION'
    with pytest.raises(RuntimeError, match='advisory lock waiters'):
        p.effect_gate(*args)
