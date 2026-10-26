"""Offline regression gates; no Docker, HTTP or banking requests."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# Load the actual bridge without importing heavy server/experiment dependencies.
PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'faultweave_v2_isolated_telemetry_bridge.py'
SPEC = importlib.util.spec_from_file_location('fw_v2_bridge_test_module', PATH)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def test_fixed_isolated_destinations():
    assert bridge.ISOLATED == 'http://127.0.0.1:28110'
    assert bridge.SHADOW == 'http://127.0.0.1:28190'
    assert bridge.DURATION == 75.0
    assert bridge.COUNT == 113


def test_no_acknowledgment_prevents_live_capture(monkeypatch):
    monkeypatch.setattr(bridge, 'capture', lambda: pytest.fail('MUST NOT CAPTURE'))
    with pytest.raises(RuntimeError, match='explicit opt-in'):
        bridge.main(['--capture'])


def test_preview_never_contacts_network_or_docker(monkeypatch, capsys):
    monkeypatch.setattr(bridge, 'preflight', lambda: pytest.fail('MUST NOT PREFLIGHT'))
    monkeypatch.setattr(bridge, '_request_json', lambda *a, **kw: pytest.fail('MUST NOT HTTP'))
    bridge.main(['--preview'])
    assert 'READ ONLY' in capsys.readouterr().out


def test_reject_non_loopback_or_primary_gateway_before_http():
    for dest in (
        'http://127.0.0.1:18110', 'http://localhost:28110',
        'https://127.0.0.1:28110', 'http://evil.com',
        'http://127.0.0.1:28110.evil.com/path',
    ):
        with pytest.raises(RuntimeError, match='unapproved HTTP target'):
            bridge._request_json(dest)


@pytest.mark.parametrize('bad', [
    (200, {}),
    (409, {'detail': 'coverage 4 seconds'}),
    (404, {'detail': 'not found'}),
])
def test_live_buffer_must_be_truly_empty(monkeypatch, bad):
    monkeypatch.setattr(bridge, '_request_json', lambda url, payload=None, timeout=20: bad)
    with pytest.raises(RuntimeError, match='not provably empty'):
        bridge.require_empty_buffer()


def test_properly_empty_buffer_passes(monkeypatch):
    calls = []
    def probe(url, payload=None, timeout=20):
        calls.append(url)
        return 409, {'detail':'no live telemetry has been observed'}
    monkeypatch.setattr(bridge, '_request_json', probe)
    bridge.require_empty_buffer()
    assert len(calls) == 2
    assert calls[0].endswith('/10') and calls[1].endswith('/60')


def test_require_200_never_hides_rejections(monkeypatch):
    monkeypatch.setattr(bridge, '_request_json', lambda *a, **kw: (422, {'detail':'bad telemetry'}))
    with pytest.raises(RuntimeError, match='HTTP 422'):
        bridge.require_200(bridge.ISOLATED+'/api/v1/intelligence/telemetry', {})


def test_run_provenance_protected_at_client_wire():
    with pytest.raises(RuntimeError, match='cross-run'):
        bridge.request_wire({'run_id':'other','scenario':'valid'}, 'v2bridge-1234567890abcdef')


def test_pilot_must_use_healthy_valid_requests():
    with pytest.raises(RuntimeError, match='only permits healthy'):
        bridge.request_wire({'run_id':'a','scenario':'invalid_login'}, 'a')


def test_run_provenance_protected_at_event_wire():
    with pytest.raises(RuntimeError, match='cross-run'):
        bridge.event_wire({'run_id':'other','service':'gateway'}, 'current')


def test_unexpected_service_rejected():
    with pytest.raises(RuntimeError, match='unexpected service'):
        bridge.event_wire({'run_id':'current','service':'untrusted'}, 'current')


def test_strict_comparison_happy_path_and_feature_drift(monkeypatch):
    from types import ModuleType
    fake = ModuleType('datasets.final_features')
    fake.FEATURE_NAMES = tuple(f'feature_{i}' for i in range(65))
    monkeypatch.setitem(sys.modules, 'datasets.final_features', fake)
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(seconds=60)
    win = types.SimpleNamespace(started_at=start, ended_at=now)
    expected = {k: float(i) for i, k in enumerate(fake.FEATURE_NAMES)}
    snap = {
        'window_seconds':60,
        'request_observation_source':'client_observed',
        'window_started_at': start.isoformat(),
        'window_ended_at': now.isoformat(),
        'coverage_seconds': 70.0,
        'request_count': 80,
        'event_count': 500,
        'features': dict(expected),
    }
    bridge.strict_comparison(snap, expected, win)
    snap['features']['feature_3'] = -100.0
    with pytest.raises(RuntimeError, match='LIVE_V2_FEATURE_DRIFT'):
        bridge.strict_comparison(snap, expected, win)


def test_strict_comparison_needs_independent_clients(monkeypatch):
    fake = types.ModuleType('datasets.final_features')
    fake.FEATURE_NAMES = tuple(f'f{i}' for i in range(65))
    monkeypatch.setitem(sys.modules, 'datasets.final_features', fake)
    now = datetime.now(UTC)
    win = types.SimpleNamespace(started_at=now - timedelta(seconds=60), ended_at=now)
    snap = {'window_seconds':60, 'request_observation_source':'gateway_derived'}
    with pytest.raises(RuntimeError, match='independently observed'):
        bridge.strict_comparison(snap, {}, win)


def test_ingest_batches_under_500_and_accounts_for_all(monkeypatch):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [{'timestamp': (base + timedelta(milliseconds=i*100)).isoformat(), 'seq':i} for i in range(625)]
    clients = [{'started_at': (base + timedelta(milliseconds=i*100)).isoformat(), 'seq':i} for i in range(78)]
    posts = []
    def submit(url, payload=None, timeout=20):
        assert url == bridge.ISOLATED + '/api/v1/intelligence/telemetry'
        posts.append(payload)
        assert len(payload['events']) + len(payload['requests']) <= 250
        return {'status':'accepted','accepted':len(payload['events']),
                'accepted_requests':len(payload['requests']), 'duplicates':0, 'duplicate_requests':0}
    monkeypatch.setattr(bridge, 'require_200', submit)
    bridge.ingest(events, clients)
    assert len(posts) == 3
    assert sum(map(lambda x: len(x['events']), posts)) == 625
    assert sum(map(lambda x: len(x['requests']), posts)) == 78


def test_ingest_fail_closed_on_deduplication(monkeypatch):
    now = datetime.now(UTC).isoformat()
    def submit(*args, **kwargs):
        return {'status':'accepted','accepted':0,'accepted_requests':0,'duplicates':1,'duplicate_requests':0}
    monkeypatch.setattr(bridge,'require_200',submit)
    with pytest.raises(RuntimeError, match='deduplicated'):
        bridge.ingest([{'timestamp':now}], [])


def test_intent_is_create_only(tmp_path):
    path = tmp_path/'result.json'
    bridge.write_new(path, {'status':'STARTED'})
    assert json.loads(path.read_text())['status'] == 'STARTED'
    with pytest.raises(RuntimeError, match='overwrite'):
        bridge.write_new(path, {'status':'OVERWRITTEN'})
    assert json.loads(path.read_text())['status'] == 'STARTED'


def test_utc_rejects_naive_timestamps():
    with pytest.raises(RuntimeError, match='timezone'):
        bridge.utc('2026-01-01T00:00:00')


def test_no_raw_access_to_protected_label_or_retrain():
    source = PATH.read_text(encoding='utf-8')
    for keyword in ('joblib.dump(', 'model.fit(', 'docker compose down', 'fault_id =', 'os.remove('):
        assert keyword not in source


def test_event_wire_redacts_messages_and_arbitrary_attributes(monkeypatch):
    outer = types.ModuleType('faultweave_common')
    inner = types.ModuleType('faultweave_common.logging')
    class FakeEvent:
        model_fields = {'attributes':object()}
        @classmethod
        def model_validate(cls, record):
            assert record['message'] == 'Observed structured microservice event'
            assert record['attributes'] == {}
            assert 'secret' not in record
            return types.SimpleNamespace(model_dump=lambda **kw: record)
    inner.StructuredLogEvent = FakeEvent
    monkeypatch.setitem(sys.modules, 'faultweave_common', outer)
    monkeypatch.setitem(sys.modules, 'faultweave_common.logging', inner)
    event = bridge.event_wire({'run_id':'run','service':'gateway',
        'message':'password=shhhh', 'attributes':{'secret':'password'},
        'event_type':'http_request_completed'}, 'run')
    assert 'shhhh' not in str(event)
    assert 'password' not in str(event)


def test_ingest_failure_must_not_be_retried(monkeypatch):
    now = datetime.now(UTC).isoformat()
    counter = {'n':0}
    def reject(*a, **kw):
        counter['n'] += 1
        raise RuntimeError('simulated partial ingestion failure')
    monkeypatch.setattr(bridge,'require_200',reject)
    with pytest.raises(RuntimeError, match='simulated partial'):
        bridge.ingest([{'timestamp':now}], [])
    assert counter['n'] == 1
