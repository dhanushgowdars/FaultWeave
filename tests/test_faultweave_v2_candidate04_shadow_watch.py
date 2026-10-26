"""All offline: tests are detached from Docker, gateway, model bundle and network."""
from __future__ import annotations
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'faultweave_v2_candidate04_shadow_watch.py'
spec = importlib.util.spec_from_file_location('fw_shadow_watch', MODULE)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
NAMES = tuple('field_%02d' % i for i in range(65))
NOW = datetime(2026, 10, 14, 18, 0, 0, tzinfo=timezone.utc)


def snapshot(**changes):
    obj = {
        'window_seconds': 60,
        'request_observation_source': 'client_observed',
        'window_started_at': (NOW - timedelta(seconds=68)).isoformat(),
        'window_ended_at': (NOW - timedelta(seconds=8)).isoformat(),
        'coverage_seconds': 72,
        'request_count': 90,
        'event_count': 2250,
        'features': {k: i * 1.25 for i, k in enumerate(NAMES)},
    }
    obj.update(changes)
    return obj


def test_fixed_endpoints_and_hash():
    assert watch.GATEWAY.endswith(':28110')
    assert watch.SHADOW.endswith(':28190')
    assert len(watch.MODEL_SHA256) == 64


def test_preview_no_imports_network_or_writes(monkeypatch, capsys):
    monkeypatch.setattr(watch, 'check_contract', lambda: pytest.fail('NO PREFLIGHT'))
    monkeypatch.setattr(watch, 'api', lambda *args, **kw: pytest.fail('NO NETWORK'))
    assert watch.main(['--preview']) == 0
    assert 'NOT a continuous telemetry collector' in capsys.readouterr().out


@pytest.mark.parametrize('mode', ['--once','--watch'])
def test_ack_required_before_active_mode(monkeypatch, mode):
    monkeypatch.setattr(watch, 'check_contract', lambda: pytest.fail('NO MODEL'))
    with pytest.raises(watch.WatchError, match='acknowledgement required'):
        watch.main([mode])


@pytest.mark.parametrize('dest', ['http://127.0.0.1:18110',
   'http://127.0.0.1:28110.evil.net/x', 'https://127.0.0.1:28110',
   'http://example.net', 'http://localhost:28110',
])
def test_disallow_unapproved_hosts_before_network(dest):
    with pytest.raises(watch.WatchError, match='unapproved HTTP'):
        watch.api(dest)


def test_never_post_to_telemetry():
    with pytest.raises(watch.WatchError, match='POST permitted only'):
        watch.api(watch.GATEWAY + '/api/v1/intelligence/telemetry', {'events': []})


def test_good_window():
    checked = watch.snapshot_gate(snapshot(), NAMES, now=NOW)
    assert checked['kind'] == 'READY'
    assert checked['request_count'] == 90
    assert len(checked['features']) == 65


def test_stale_window_is_skipped_without_inference():
    end = NOW-timedelta(minutes=10)
    result = watch.snapshot_gate(snapshot(window_started_at=(end-timedelta(seconds=60)).isoformat(),
                                          window_ended_at=end.isoformat()), NAMES, now=NOW)
    assert result['kind'] == 'SKIP_STALE'


def test_future_watermark_fail_closed():
    end = NOW + timedelta(seconds=8)
    with pytest.raises(watch.WatchError, match='future'):
        watch.snapshot_gate(snapshot(window_started_at=(end-timedelta(seconds=60)).isoformat(),
                                     window_ended_at=end.isoformat()), NAMES, now=NOW)


@pytest.mark.parametrize('changes, error', [
    ({'window_seconds': 10}, 'exact 60'),
    ({'request_observation_source': 'gateway_derived'}, 'independently observed'),
    ({'coverage_seconds': 59.99}, 'incomplete'),
    ({'request_count': 69}, 'insufficient client'),
    ({'event_count': 0}, 'insufficient service'),
    ({'window_started_at': (NOW-timedelta(seconds=65)).isoformat()}, 'not exactly'),
])
def test_quality_controls(changes,error):
    with pytest.raises(watch.WatchError, match=error):
        watch.snapshot_gate(snapshot(**changes), NAMES, now=NOW)


@pytest.mark.parametrize('bad', [None, True, '1.2', float('nan'), float('inf')])
def test_bad_features_rejected(bad):
    s = snapshot()
    s['features']['field_08'] = bad
    with pytest.raises(watch.WatchError, match='non-finite'):
        watch.snapshot_gate(s, NAMES, now=NOW)


def test_missing_extra_feature_rejected():
    s = snapshot()
    del s['features']['field_12']
    with pytest.raises(watch.WatchError, match='differ'):
        watch.snapshot_gate(s, NAMES, now=NOW)


def test_duplicate_window_receipt_never_overwritten(tmp_path):
    f = tmp_path/'receipt.json'
    first = {'window_ended_at':'123', 'feature_vector_sha256':'abc', 'prediction':'NORMAL', 'model_sha256':'XYZ'}
    assert watch.persist(f, first)
    original = f.read_bytes()
    assert watch.persist(f, first) is False
    assert f.read_bytes() == original
    with pytest.raises(watch.WatchError, match='conflicting inference'):
        watch.persist(f, {**first, 'prediction': 'UNKNOWN ABNORMAL PATTERN'})
    assert f.read_bytes() == original


def test_conflicting_existing_snapshot_blocks_without_prediction(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'OUT', tmp_path)
    s = snapshot()
    good = watch.snapshot_gate(s, NAMES, now=NOW)
    p = watch.receipt_path(good['ended_at'])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({'window_ended_at':good['ended_at'],'feature_vector_sha256':'WRONG',
        'model_sha256':watch.MODEL_SHA256}))
    monkeypatch.setattr(watch, 'api', lambda u,payload=None:(200,s) if payload is None else pytest.fail('NO INFER'))
    with pytest.raises(watch.WatchError, match='existing window receipt differs'):
        watch.one_poll(NAMES, infer=True, now=NOW)


def test_409_produces_wait_not_inference(monkeypatch):
    monkeypatch.setattr(watch, 'api', lambda u,payload=None:(409,{'detail':'no live telemetry has been observed'}))
    assert watch.one_poll(NAMES, infer=True, now=NOW) == 'WAIT'


def test_stale_produces_skip_not_inference(monkeypatch):
    s = snapshot(window_started_at=(NOW-timedelta(minutes=12)).isoformat(),
                 window_ended_at=(NOW-timedelta(minutes=11)).isoformat())
    monkeypatch.setattr(watch, 'api', lambda u,payload=None:(200,s) if payload is None else pytest.fail('NO INFER'))
    assert watch.one_poll(NAMES, infer=True, now=NOW) == 'STALE'


def test_read_only_check_never_infers_or_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'OUT', tmp_path)
    monkeypatch.setattr(watch, 'api', lambda u,payload=None:(200,snapshot()) if payload is None else pytest.fail('NO INFER'))
    assert watch.one_poll(NAMES, infer=False, now=NOW) == 'READY'
    assert not list(tmp_path.iterdir())


def test_live_http_model_inference_records_research_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'OUT', tmp_path)
    calls = []
    def fake(u, payload=None):
        calls.append((u,payload))
        if u.endswith('/ready'):
            return (200,{'model_sha256':watch.MODEL_SHA256,
                         'deployment_status':'RESEARCH_ONLY_NOT_DEPLOYED'})
        if u.endswith('/infer'):
            assert payload['window_seconds'] == 60
            assert len(payload['features']) == 65
            return (200,{'decision':{'prediction':'NORMAL'}})
        return 200, snapshot()
    monkeypatch.setattr(watch, 'api', fake)
    assert watch.one_poll(NAMES, infer=True, now=NOW) == 'PREDICTED'
    files = list(tmp_path.glob('*.prediction.json'))
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert record['prediction'] == 'NORMAL' and record['training_eligible'] is False
    assert record['threshold_tuning_eligible'] is False
    assert sum(1 for u,p in calls if p is not None) == 1
    assert watch.one_poll(NAMES, infer=True, now=NOW) == 'DUPLICATE'
    assert sum(1 for u,p in calls if p is not None) == 1


def test_preflight_no_inference_or_writes(monkeypatch):
    monkeypatch.setattr(watch, 'check_contract', lambda: NAMES)
    monkeypatch.setattr(watch, 'one_poll', lambda *a,**k: pytest.fail('NO POLL'))
    assert watch.main(['--preflight']) == 0


def test_poll_bounds(monkeypatch):
    monkeypatch.setattr(watch, 'check_contract', lambda: pytest.fail('NO MODEL'))
    with pytest.raises(watch.WatchError, match='poll interval'):
        watch.main(['--preflight','--poll-seconds','1'])


def test_source_has_no_docker_mutations_or_ingestion():
    content = MODULE.read_text()
    assert 'docker compose' not in content.lower()
    assert "'/api/v1/intelligence/telemetry'" not in content
    assert 'joblib.dump(' not in content
