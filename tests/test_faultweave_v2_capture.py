"""Offline safety and evidence tests. No Docker, network, or live traffic."""
import hashlib
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import faultweave_v2_capture as cap
from scripts import faultweave_v2_independent_audit as verify


def test_slots_are_precommitted():
    assert cap.split_for('NORMAL', 1) == 'train'
    assert cap.split_for('NORMAL', 13) == 'validation'
    assert cap.split_for('NORMAL', 16) == 'test'
    assert cap.split_for(cap.FAULT, 8) == 'train'
    assert cap.split_for(cap.FAULT, 9) == 'validation'
    assert cap.split_for(cap.FAULT, 11) == 'test'
    with pytest.raises(ValueError):
        cap.split_for('DATABASE_UNAVAILABLE', 1)
    with pytest.raises(ValueError):
        cap.split_for('NORMAL', 19)


def test_preview_does_not_touch_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cap, 'ROOT', tmp_path)
    monkeypatch.setattr(cap, 'RUNS', tmp_path/'data/datasets/v2/training-captures')
    assert cap.main(['--preview','--label','NORMAL','--slot','1']) == 0
    assert list(tmp_path.iterdir()) == []
    assert 'PREVIEW ONLY' in capsys.readouterr().out


def test_no_accidental_capture_without_acknowledgement():
    with pytest.raises(ValueError, match='Explicit'):
        cap.main(['--capture','--label','NORMAL','--slot','1'])


@pytest.fixture
def sample(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(cap, 'ROOT', root)
    monkeypatch.setattr(cap, 'RUNS', root/'data/datasets/v2/training-captures')
    monkeypatch.setattr(cap, 'CONTROL', root/'data/datasets/v2/control')
    monkeypatch.setattr(cap, 'LOCK', root/'data/datasets/v2/control/v2-training-active')
    monkeypatch.setattr(cap, 'SUPERVISOR_LOCK', root/'data/datasets/v2/control/phase19c2k-active')
    monkeypatch.setattr(verify, 'ROOT', root)
    monkeypatch.setattr(verify, 'run_dir', cap.run_dir)
    names = tuple(f'f_{i:02d}' for i in reversed(range(65)))
    features = types.ModuleType('datasets.final_features')
    features.FEATURE_NAMES = names
    windows = types.ModuleType('datasets.phase19c2b_exact_windows')
    def exact(**kwargs):
        end = kwargs['phase_ended_at'] - timedelta(seconds=2)
        return types.SimpleNamespace(started_at=end-timedelta(seconds=60), ended_at=end)
    def make_vector(**kwargs):
        return {name: float(len(kwargs['requests'])) for name in names}
    windows.select_exact_window = exact
    windows.build_observable_features = make_vector
    healthy = types.ModuleType('scripts.phase19c2c_healthy_pilot')
    def correlated(requests, events):
        if not requests or any(r['expected_outcome'] is not True for r in requests):
            raise ValueError('healthy incorrect')
        return None
    healthy.validate_correlations = correlated
    healthy.jsonl_bytes = lambda rows: b''.join((json.dumps(r, sort_keys=True) + '\n').encode() for r in rows)
    fault = types.ModuleType('scripts.phase19c2d_fault_pilot')
    fault.parsed_time = lambda value: datetime.fromisoformat(str(value).replace('Z','+00:00'))
    fault.correlation_gate = lambda requests, events: None
    fault.recovery_gate = lambda requests, events, cleared_at: {'successful_requests': 8}
    fault.fault_evidence_gate = lambda window, requests, events: {'correlated_payment_500_events': 10}
    monkeypatch.setitem(sys.modules, 'datasets', types.ModuleType('datasets'))
    monkeypatch.setitem(sys.modules, 'datasets.final_features', features)
    monkeypatch.setitem(sys.modules, 'datasets.phase19c2b_exact_windows', windows)
    monkeypatch.setitem(sys.modules, 'scripts.phase19c2c_healthy_pilot', healthy)
    monkeypatch.setitem(sys.modules, 'scripts.phase19c2d_fault_pilot', fault)
    start = datetime(2026,10,12,1,0,tzinfo=UTC)
    end = start + timedelta(seconds=75)
    run_id = 'v2train-1234567890abcdef'
    directory = cap.run_dir('NORMAL',1)
    directory.mkdir(parents=True)
    intent = {'schema_version': 'faultweave-v2-training-intent.1',
              'purpose': 'new independent training capture, NOT historical/diagnostic promotion',
              'run_id': run_id, 'label': 'NORMAL', 'slot': 1, 'split': 'train',
              'scenario_type': 'normal', 'target': None, 'intensity': None,
              'rps': 1.5, 'duration_seconds': 75,
              'collection_started_at': (start-timedelta(seconds=1)).isoformat()}
    cap.write_new(directory/'intent.json', cap.serial(intent))
    requests = []
    events = []
    for i in range(round(75*1.5)):
        t = start + timedelta(seconds=i/1.5)
        rid = f'{run_id}-request-{i:05d}'
        requests.append({'run_id':run_id,'request_id':rid,'trace_id':f'trace{i}',
                         'scenario':'valid','started_at':t.isoformat(),
                         'status_code':200,'expected_outcome':True,'transport_error':None})
        events.append({'run_id':run_id,'request_id':rid,'trace_id':f'trace{i}',
                       'timestamp':(t+timedelta(milliseconds=100)).isoformat(),
                       'service':'gateway','event_type':'http_request_completed','success':True})
    window = exact(phase_ended_at=end)
    vector = make_vector(requests=requests)
    metadata = {'phase_started_at':start.isoformat(),'phase_ended_at':end.isoformat(),
                'telemetry_watermark':events[-1]['timestamp'],'healthy_pre_post':True,
                'scheduled_requests':len(requests),'scheduled_rps':1.5}
    cap._emit_run(directory,run_id,'NORMAL','train',requests,[],events,window,vector,metadata,None)
    return directory, root


def test_audit_accepts_new_observations(sample):
    directory, root = sample
    result = verify.audit('NORMAL',1,write=False)
    assert result['verdict'] == 'VALID_V2_TRAINING_RUN'
    assert not (directory/'independent_audit.json').exists()


def test_admission_is_create_only(sample):
    directory, root = sample
    verify.audit('NORMAL',1,write=True)
    assert (directory/'admission_receipt.json').is_file()
    with pytest.raises(FileExistsError):
        verify.audit('NORMAL',1,write=True)


def test_tamper_refused(sample):
    directory, root = sample
    with (directory/'requests.jsonl').open('ab') as fd:
        fd.write(b'{}\n')
    with pytest.raises(ValueError, match='digest mismatch|Invalid or missing observation'):
        verify.audit('NORMAL',1)


def test_mislabeled_intent_refused(sample):
    directory, root = sample
    data=json.loads((directory/'intent.json').read_text())
    data['label']='DOWNSTREAM_ERROR_BURST'
    (directory/'intent.json').write_bytes(cap.serial(data))
    with pytest.raises(ValueError, match='intent mismatch'):
        verify.audit('NORMAL',1)


def test_wrong_feature_refused(sample):
    directory, root=sample
    row=json.loads((directory/'feature_row.json').read_text())
    row['features']['f_00']=991
    (directory/'feature_row.json').write_bytes(cap.serial(row))
    with pytest.raises(ValueError, match='digest mismatch|Invalid or missing observation'):
        verify.audit('NORMAL',1)


def test_supervisor_lock_refusal(tmp_path,monkeypatch):
    monkeypatch.setattr(cap,'SUPERVISOR_LOCK',tmp_path/'phase19c2k-active')
    monkeypatch.setattr(cap,'LOCK',tmp_path/'v2-training-active')
    cap.SUPERVISOR_LOCK.mkdir()
    with pytest.raises(RuntimeError, match='supervisor'):
        cap.clean_lock()


def test_create_only_capture_intent(sample):
    directory, root = sample
    with pytest.raises(FileExistsError):
        cap._created_new_path('NORMAL', 1, 'v2train-abcdefabcdefabcd', 'train')
    # Old accepted row and intent cannot be overwritten.
    assert json.loads((directory/'intent.json').read_text())['label'] == 'NORMAL'


def test_owner_mismatch_preserves_capture_reservation(tmp_path, monkeypatch):
    lock = tmp_path / 'v2-training-active'
    lock.mkdir()
    (lock/'owner.json').write_bytes(cap.serial({'run_id': 'v2train-aaaaaaaaaaaaaaaa'}))
    monkeypatch.setattr(cap, 'LOCK', lock)
    with pytest.raises(RuntimeError, match='owner mismatch'):
        cap.release_own_lock('v2train-bbbbbbbbbbbbbbbb')
    assert lock.exists()


def test_run_has_to_be_new_not_diagnostic(sample):
    directory, root = sample
    intent=json.loads((directory/'intent.json').read_text())
    intent['run_id']='v2fault-1234567890abcdef'
    (directory/'intent.json').write_bytes(cap.serial(intent))
    with pytest.raises(ValueError, match='intent mismatch'):
        verify.audit('NORMAL',1)
