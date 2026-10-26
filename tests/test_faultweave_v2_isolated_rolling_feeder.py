"""Offline safeguards for the bounded rolling feeder (no Docker or HTTP I/O)."""
import json
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from scripts import faultweave_v2_isolated_rolling_feeder as f


def install_schema(monkeypatch, *, retention=180, eligible=lambda x: True):
    module = types.ModuleType('services.gateway.app.intelligence_live')
    module.MAX_BUFFER_SECONDS = retention
    module.eligible_runtime_event = eligible
    monkeypatch.setitem(sys.modules, 'services.gateway.app.intelligence_live', module)


def test_preview_does_not_use_network(monkeypatch, capsys):
    monkeypatch.setattr(f, 'preflight', lambda: pytest.fail('preview attempted preflight'))
    assert f.main(['--preview']) == 0
    assert 'READ ONLY' in capsys.readouterr().out


def test_feed_requires_explicit_ack(monkeypatch):
    monkeypatch.setattr(f, 'feed', lambda: pytest.fail('executed without ack'))
    with pytest.raises(RuntimeError, match='explicit consent'):
        f.main(['--feed'])


def test_mutually_exclusive_modes():
    with pytest.raises(SystemExit):
        f.main(['--preview', '--preflight'])


def test_empty_live_buffer_allowed(monkeypatch):
    install_schema(monkeypatch)
    monkeypatch.setattr(f.bridge, '_request_json', lambda url: (409, {'detail':'no live telemetry has been observed'}))
    assert f.no_recent_data() == 'EMPTY'


def test_old_buffer_allowed(monkeypatch):
    install_schema(monkeypatch)
    now = datetime.now(UTC)
    end = (now - timedelta(seconds=240)).isoformat()
    monkeypatch.setattr(f.bridge, '_request_json', lambda url: (200, {'window_ended_at': end, 'request_observation_source': 'client_observed'}))
    assert f.no_recent_data(now=now).startswith('STALE_PRIOR_BUFFER')


@pytest.mark.parametrize('age', [0, 50, 180, 199.9, -10])
def test_recent_buffer_refused(monkeypatch, age):
    install_schema(monkeypatch)
    now = datetime.now(UTC)
    end = (now - timedelta(seconds=age)).isoformat()
    monkeypatch.setattr(f.bridge, '_request_json', lambda url: (200, {'window_ended_at':end, 'request_observation_source':'client_observed'}))
    with pytest.raises(RuntimeError, match='no run mixing'):
        f.no_recent_data(now=now)


def test_gateway_unknown_conflict_fails_closed(monkeypatch):
    install_schema(monkeypatch)
    monkeypatch.setattr(f.bridge, '_request_json', lambda url: (409, {'detail':'insufficient coverage'}))
    with pytest.raises(RuntimeError, match='ambiguous'):
        f.no_recent_data()


def test_gateway_retention_change_fails(monkeypatch):
    install_schema(monkeypatch, retention=300)
    monkeypatch.setattr(f.bridge, '_request_json', lambda url: pytest.fail('called HTTP despite retention mismatch'))
    with pytest.raises(RuntimeError, match='retention changed'):
        f.no_recent_data()


def test_old_buffer_rejects_untrusted_provenance(monkeypatch):
    install_schema(monkeypatch)
    end = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
    monkeypatch.setattr(f.bridge, '_request_json', lambda url: (200, {'window_ended_at':end, 'request_observation_source':'gateway_derived'}))
    with pytest.raises(RuntimeError, match='untrusted'):
        f.no_recent_data()


def test_new_rows_deduplicates_observed_logs_and_links_requests(monkeypatch):
    install_schema(monkeypatch)
    helper = types.ModuleType('scripts.phase19c2c_healthy_pilot')
    helper.visible_events = lambda items, rid: items
    helper.proven_client_rows = lambda rows, rid: [dict(x, run_id=rid) for x in rows]
    monkeypatch.setitem(sys.modules, 'scripts.phase19c2c_healthy_pilot', helper)
    monkeypatch.setattr(f.bridge, 'event_wire', lambda ev, rid: ev)
    monkeypatch.setattr(f.bridge, 'request_wire', lambda req, rid: req)
    run = 'v2rolling-1234567890abcdef'
    ev = {'run_id':run,'request_id':run+'-request-1','trace_id':'trace-one','timestamp':datetime.now(UTC).isoformat(), 'service':'gateway'}
    rid = ev['request_id']
    req = {'request_id':rid,'trace_id':'trace-one','started_at':ev['timestamp'], 'expected_outcome':True, 'status_code':200,'transport_error':None}
    events, requests, ids, req_ids = f.new_rows([ev, ev], {rid:req}, run, set(), set())
    assert len(events) == len(requests) == len(ids) == len(req_ids) == 1
    events, requests, ids, req_ids = f.new_rows([ev], {rid:req}, run, ids, req_ids)
    assert not events and not requests and not ids and not req_ids


def test_new_rows_rejects_mismatched_traces(monkeypatch):
    install_schema(monkeypatch)
    helper = types.ModuleType('scripts.phase19c2c_healthy_pilot')
    helper.visible_events = lambda items, rid: items
    helper.proven_client_rows = lambda rows, rid: rows
    monkeypatch.setitem(sys.modules, 'scripts.phase19c2c_healthy_pilot', helper)
    monkeypatch.setattr(f.bridge, 'event_wire', lambda ev, rid: ev)
    run = 'v2rolling-1234567890abcdef'
    rid = run+'-request-1'
    ev = {'run_id':run,'request_id':rid,'trace_id':'wrong','timestamp':datetime.now(UTC).isoformat(),'service':'gateway'}
    req = {'request_id':rid,'trace_id':'correct','started_at':ev['timestamp'],'expected_outcome':True,'status_code':200,'transport_error':None}
    with pytest.raises(RuntimeError, match='trace mismatch'):
        f.new_rows([ev], {rid:req}, run, set(), set())


def test_post_once_rejects_duplicate_ack(monkeypatch):
    monkeypatch.setattr(f.bridge, 'require_200', lambda url, data: {'status':'accepted','ignored':0, 'duplicates':1, 'duplicate_requests':0, 'accepted':1,'accepted_requests':0})
    event = {'timestamp':datetime.now(UTC).isoformat(), 'request_id':'r'}
    with pytest.raises(RuntimeError, match='partially accepted'):
        f.post_once([event], [])


def test_post_once_has_no_shadow_post(monkeypatch):
    called = []
    def accept(url, body):
        called.append(url)
        return {'status':'accepted','ignored':0,'duplicates':0,'duplicate_requests':0,'accepted':len(body['events']), 'accepted_requests':len(body['requests'])}
    monkeypatch.setattr(f.bridge, 'require_200', accept)
    f.post_once([{'timestamp': datetime.now(UTC).isoformat(), 'request_id':'r'}], [])
    assert called == [f.GATEWAY + '/api/v1/intelligence/telemetry']


def test_existing_lock_stops_before_network_or_docker(monkeypatch, tmp_path):
    monkeypatch.setattr(f, 'OUT', tmp_path)
    (tmp_path / 'active.lock').write_text('preserve')
    with pytest.raises(RuntimeError, match='prior rolling feeder lock'):
        f.preflight()
