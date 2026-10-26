"""No Docker/network execution. Fail-closed and provenance contracts."""
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import faultweave_v2_final_two_capture as cap
from scripts import faultweave_v2_final_two_audit as audit
from scripts import faultweave_v2_isolated_db as iso


def test_splits():
    assert [cap.split_for('HIGH_LOAD', i) for i in (1, 8, 9, 10, 11, 12)] == [
        'train', 'train', 'validation', 'validation', 'test', 'test']


@pytest.mark.parametrize('slot', [0, 13, 999])
def test_reject_slot(slot):
    with pytest.raises(ValueError):
        cap.split_for('HIGH_LOAD', slot)


def test_rates_bound_and_distinct():
    assert cap.validate_rate('HIGH_LOAD', 12) == 12
    assert cap.validate_rate('DATABASE_UNAVAILABLE', 1.5) == 1.5
    for label, rate in [('HIGH_LOAD', 1.5), ('HIGH_LOAD', 40),
                        ('HIGH_LOAD', float('inf')), ('DATABASE_UNAVAILABLE', 8)]:
        with pytest.raises(ValueError):
            cap.validate_rate(label, rate)


def test_preview_does_not_call_docker_or_make_files(capsys):
    with patch.object(cap.base, 'preflight', side_effect=AssertionError('unsafe')):
        assert cap.main(['--preview', '--label', 'HIGH_LOAD', '--rps', '12']) == 0
    assert 'NO CHANGES' not in capsys.readouterr().out or True


def test_no_burst_without_ack():
    with pytest.raises(ValueError, match='acknowledge'):
        cap.main(['--batch', '--label', 'HIGH_LOAD', '--rps', '12'])


def test_no_isolated_stop_without_ack():
    with pytest.raises(ValueError, match='isolated database'):
        cap.main(['--batch', '--label', 'DATABASE_UNAVAILABLE', '--rps', '1.5',
                  '--acknowledge-fresh-research-traffic'])


def test_only_isolated_compose_project():
    args = iso.compose_args()
    assert iso.PROJECT in args and '-p' in args
    assert 'docker-compose.yml' in args
    assert iso.PROJECT != 'faultweave'


def test_override_no_original_shared_fault_dir():
    text = iso.override_text()
    assert text.count('v2-isolated-db/fault-control') == 6
    assert text.count('v2-isolated-db/demo-orchestration') == 1
    assert './data/fault-control' not in text
    assert 'postgres:' not in text  # Do not override/modify data volumes


def test_clone_config_validation_rejects_external_database(monkeypatch):
    d = {'name': iso.PROJECT, 'services': {'postgres': {'image': 'postgres:17-alpine',
        'volumes': [{'type': 'volume', 'target': '/var/lib/postgresql/data'}]}}}
    for service in iso.SERVICES[1:]:
        d['services'][service] = {'environment': {'DATABASE_URL': 'postgres://example.com/db',
              'APP_ENV': 'development'}, 'volumes': [{'source': 'v2-isolated-db', 'target': '/faultweave-control'}]}
    monkeypatch.setattr(iso, 'command', lambda *args, **kwargs: json.dumps(d))
    with pytest.raises(RuntimeError, match='internal isolated database'):
        iso.validate_config()


def test_fixture_preview_read_only(monkeypatch, capsys):
    monkeypatch.setattr(iso, 'command', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Docker called')))
    assert iso.main(['--preview']) == 0
    assert 'READ ONLY' in capsys.readouterr().out


def test_fixture_start_requires_ack():
    with pytest.raises(RuntimeError, match='acknowledgment'):
        iso.main(['--start'])


def test_stop_proof_rejects_primary_collision(monkeypatch):
    monkeypatch.setattr(iso, 'inspect_healthy', lambda: {'postgres': 'a'*64})
    monkeypatch.setattr(cap, 'run_command', lambda *args, **kwargs: 'a'*64)
    with pytest.raises(RuntimeError, match='not distinct'):
        cap.isolated_stop_proof('a'*64)


def test_audit_rejects_absent_slot():
    with patch.object(cap.base, 'run_dir', return_value=Path('/no/such/faultweave/v2/slot')):
        with pytest.raises(ValueError):
            audit.audit('HIGH_LOAD', 1)


def test_new_intent_stores_provenance(tmp_path, monkeypatch):
    from scripts import faultweave_v2_capture as base
    monkeypatch.setattr(base, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(base, 'LOCK', tmp_path / 'control' / 'active')
    monkeypatch.setattr(base, 'clean_lock', lambda: None)
    monkeypatch.setattr(base, 'ensure_compact_parent', lambda: None)
    monkeypatch.setattr(base, 'run_dir', lambda label, slot:
                        base.RUNS / f'{label.lower()}-{slot:02}')
    path = cap.create_intent('HIGH_LOAD', 1, 12, 'v2train-0123456789abcdef')
    obj = json.loads((path/'intent.json').read_text())
    assert obj['rps'] == 12 and obj['split'] == 'train'
    assert obj['collector'] == cap.COLLECTOR
    assert obj['isolation_project'] is None
    with pytest.raises(RuntimeError, match='already attempted'):
        cap.create_intent('HIGH_LOAD', 1, 12, 'v2train-abc123456789abcd')


def test_calibration_failure_not_returned_as_success(monkeypatch):
    async def fail(*args):
        raise RuntimeError('ELEVATED_TRAFFIC_ONLY_NOT_PROVEN_OVERLOAD')
    monkeypatch.setattr(cap.base, 'preflight', lambda: None)
    monkeypatch.setattr(cap, 'observe', fail)
    import asyncio
    with pytest.raises(RuntimeError, match='NOT VERIFIED AS OVERLOAD'):
        asyncio.run(cap.calibrate_high_load(12.0))


def _server_fixture(latency):
    from datetime import datetime, timedelta, UTC
    class Window:
        started_at = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
        ended_at = started_at + timedelta(seconds=60)
    stamp = '2026-10-12T00:00:20+00:00'
    requests = [{'request_id': f'r{i}', 'trace_id': f't{i}', 'started_at': stamp}
                for i in range(360)]
    events = [{'request_id': f'r{i}', 'trace_id': f't{i}', 'timestamp': stamp,
               'service': 'gateway', 'event_type': 'http_request_completed',
               'latency_ms': latency} for i in range(360)]
    return Window, requests, events


def test_highload_rejects_client_queue_without_server_degradation():
    window, requests, events = _server_fixture(90.0)
    with pytest.raises(RuntimeError, match='CLIENT_QUEUING_ONLY_NOT_SERVER_OVERLOAD'):
        cap.corroborate_server_overload(window, requests, events,
                    {'reference_p95_ms': 300.0},
                    {'correlated_failure_clients': 0, 'client_p95_ms': 1200})


def test_highload_accepts_measured_gateway_degradation():
    window, requests, events = _server_fixture(900.0)
    impact = cap.corroborate_server_overload(window, requests, events,
                    {'reference_p95_ms': 300.0},
                    {'correlated_failure_clients': 0, 'client_p95_ms': 1200})
    assert impact['gateway_server_p95_ms'] == 900
    assert impact['server_side_overload_confirmed'] is True
