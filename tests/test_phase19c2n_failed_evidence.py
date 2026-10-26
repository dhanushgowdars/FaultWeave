"""Offline regression tests for fail-closed resource-effect diagnostics."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import phase19c2n_resource_pilot as p


def window():
    start = datetime(2026, 10, 11, 0, 0, 0, tzinfo=UTC)
    return SimpleNamespace(started_at=start, ended_at=start + timedelta(seconds=60))


def test_gate_error_prints_both_actual_counts():
    w = window()
    requests = [dict(request_id='r1', trace_id='t1', started_at=(w.started_at + timedelta(seconds=1)).isoformat(), status_code=200, transport_error=None)]
    events = [dict(request_id='r1', trace_id='t1', timestamp=(w.started_at + timedelta(seconds=1)).isoformat(), service='gateway', outcome='success')]
    samples = [dict(timestamp=(w.started_at + timedelta(seconds=i + 1)).isoformat(), total_connections=22, advisory_waiters=0) for i in range(5)]
    # Client coverage fails before the effect gate; verify the original lower bound is untouched.
    with pytest.raises(RuntimeError, match='insufficient client coverage'):
        p.effect_gate(w, requests, events, samples, 'DATABASE_LOCK_CONTENTION', {'baseline_connections':14,'baseline_waiters':0})
    # Enough client identities, but only 5 physical measurements and no failures.
    requests = [dict(request_id=f'r{i}',trace_id=f't{i}',started_at=(w.started_at+timedelta(seconds=i*.6 + 1)).isoformat(),status_code=200,transport_error=None) for i in range(90)]
    events = [dict(request_id=f'r{i}',trace_id=f't{i}',timestamp=(w.started_at+timedelta(seconds=i*.6 + 1)).isoformat(),service='gateway',outcome='success') for i in range(90)]
    with pytest.raises(RuntimeError) as error:
        p.effect_gate(w, requests, events, samples, 'DATABASE_LOCK_CONTENTION', {'baseline_connections':14,'baseline_waiters':0})
    msg = str(error.value)
    assert 'physical_samples=5/10' in msg
    assert 'correlated_client_failures=0/10' in msg
    assert 'in_window_requests=90' in msg
    assert 'total_physical_samples=5' in msg


def test_rejected_evidence_is_create_only_and_unlabeled(monkeypatch, tmp_path):
    monkeypatch.setattr(p, 'ROOT', tmp_path)
    w = window()
    run_id = 'v2resource-0123456789abcdef'
    rows = [dict(run_id=run_id, request_id='a', trace_id='b', started_at=w.started_at.isoformat(), status_code=500, transport_error=None)]
    events = [dict(run_id=run_id, request_id='a', trace_id='b', timestamp=w.started_at.isoformat(), service='payment', status_code=500)]
    samples = [dict(timestamp=w.started_at.isoformat(), total_connections=23, advisory_waiters=0, advisory_holders=0)]
    path = p.persist_rejected_gate_evidence(run_id, rows, [], events, samples, w, 'STOP: test effect')
    assert path == tmp_path/'data/datasets/v2/rejected-resource'/run_id
    assert sorted(x.name for x in path.iterdir()) == ['database_samples.jsonl','events.jsonl','manifest.json','recovery_requests.jsonl','requests.jsonl']
    manifest = json.loads((path/'manifest.json').read_text())
    assert manifest['verdict'] == 'REJECTED_RESOURCE_EFFECT_GATE'
    assert manifest['training_eligible'] is False
    assert manifest['threshold_tuning_eligible'] is False
    assert manifest['label'] is None
    assert manifest['feature_vector_stored'] is False
    for fname, metadata in manifest['artifacts'].items():
        raw = (path/fname).read_bytes()
        assert metadata['bytes'] == len(raw)
        assert metadata['sha256'] == hashlib.sha256(raw).hexdigest()
    original_manifest = (path/'manifest.json').read_bytes()
    with pytest.raises(RuntimeError, match='already exists'):
        p.persist_rejected_gate_evidence(run_id, rows, [], events, samples, w, 'STOP: duplicate')
    assert (path/'manifest.json').read_bytes() == original_manifest


@pytest.mark.parametrize('run_id',['v2resource-0123','v2-normal-low-01','v2resource-0123456789abcdef/evil'])
def test_rejected_evidence_rejects_bad_identity(monkeypatch,tmp_path,run_id):
    monkeypatch.setattr(p,'ROOT',tmp_path)
    with pytest.raises(ValueError, match='malformed'):
        p.persist_rejected_gate_evidence(run_id,[],[],[],[],window(),'bad')
    assert not (tmp_path/'data').exists()
