"""Offline guard tests for new five-family fresh training collector and auditor."""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace, ModuleType
from datetime import datetime, UTC, timedelta

import pytest


def _load(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / 'workspace'
    pkg = root/'scripts'; pkg.mkdir(parents=True)
    (pkg/'__init__.py').write_text('')
    old = Path(__file__).resolve().parents[1]/'scripts'
    files = {
        'faultweave_v2_capture.py': old/'faultweave_v2_capture.py',
        'faultweave_v2_independent_audit.py': old/'faultweave_v2_independent_audit.py',
        'faultweave_v2_verified_capture.py': old/'faultweave_v2_verified_capture.py',
        'faultweave_v2_verified_audit.py': old/'faultweave_v2_verified_audit.py',
    }
    for name, src in files.items():
        shutil.copy2(src, pkg/name)
    monkeypatch.syspath_prepend(str(root))
    for name in list(sys.modules):
        if name == 'scripts' or name.startswith('scripts.faultweave_v2_'):
            monkeypatch.delitem(sys.modules, name, raising=False)
    base = importlib.import_module('scripts.faultweave_v2_capture')
    audited = importlib.import_module('scripts.faultweave_v2_verified_audit')
    ext = importlib.import_module('scripts.faultweave_v2_verified_capture')
    base.ROOT = root
    base.RUNS = root/'data/datasets/v2/training-captures'
    base.CONTROL = root/'data/datasets/v2/control'
    base.LOCK = base.CONTROL/'v2-training-active'
    base.SUPERVISOR_LOCK = base.CONTROL/'phase19c2k-active'
    ext.ROOT = root
    ext.RUNS, ext.LOCK = base.RUNS, base.LOCK
    monkeypatch.setattr(base, 'ensure_compact_parent', lambda: None)
    return base, ext, audited, root


@pytest.fixture
def fw(tmp_path, monkeypatch):
    return _load(tmp_path, monkeypatch)


def test_five_verified_classes_and_no_unsafe(fw):
    _, ext, _, _ = fw
    assert len(ext.SPECS) == 5
    assert set(ext.SPECS) == {'DOWNSTREAM_TIMEOUT', 'DATABASE_HIGH_LATENCY',
                              'SERVICE_UNAVAILABLE', 'CONNECTION_POOL_EXHAUSTION',
                              'DATABASE_LOCK_CONTENTION'}
    for unsafe in ('DATABASE_UNAVAILABLE', 'HIGH_LOAD', 'AUTHENTICATION_FAILURE_BURST'):
        with pytest.raises(ValueError): ext.spec(unsafe)


def test_splits_and_invalid_slots(fw):
    _, ext, _, _ = fw
    for label in ext.SPECS:
        assert [ext.split_for(label, i) for i in (1, 8, 9, 10, 11, 12)] == [
            'train','train','validation','validation','test','test']
    for invalid in (-1, 0, 13, True, 1.1):
        with pytest.raises(ValueError): ext.split_for('DOWNSTREAM_TIMEOUT', invalid)


def test_preview_readonly(fw, capsys):
    base, ext, _, root = fw
    assert ext.main(['--preview','--label','DATABASE_HIGH_LATENCY','--start-slot','1','--count','10']) == 0
    assert 'NO mutations' in capsys.readouterr().out
    assert not (root/'data').exists()


def test_capture_needs_ack_and_resource_ack(fw, monkeypatch):
    _, ext, _, _ = fw
    with pytest.raises(ValueError, match='acknowledgement'):
        ext.main(['--batch','--label','SERVICE_UNAVAILABLE'])
    with pytest.raises(ValueError, match='db-pressure'):
        ext.batch('DATABASE_LOCK_CONTENTION', 1, 1, db_ack=False)


def test_intent_immutable_slot_and_ownership(fw):
    base, ext, _, root = fw
    runid = 'v2train-' + 'f'*16
    path = ext.create_intent('DOWNSTREAM_TIMEOUT', 1, runid)
    intent = json.loads((path/'intent.json').read_text())
    assert intent['target'] == 'payment->ledger'
    assert intent['split'] == 'train'
    assert intent['collection_started_at']
    with pytest.raises(RuntimeError, match='reservation'):
        ext.create_intent('DOWNSTREAM_TIMEOUT', 1, runid)
    base.release_own_lock(runid)
    with pytest.raises(RuntimeError, match='attempted training slot'):
        ext.create_intent('DOWNSTREAM_TIMEOUT', 1, 'v2train-'+'a'*16)


def test_batch_independently_checks_before_admitting(fw, monkeypatch):
    _, ext, _, _ = fw
    calls=[]
    async def captured(label, slot): calls.append(('capture',label,slot))
    def fake_run(cmd, cwd, check):
        calls.append(('subprocess',cmd[3],cmd[5]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(ext,'capture',captured)
    monkeypatch.setattr(ext.subprocess,'run',fake_run)
    ext.batch('SERVICE_UNAVAILABLE', 1, 2, db_ack=False)
    assert [c[0] for c in calls] == ['capture','subprocess','subprocess']*2
    assert [c[1] for c in calls if c[0]=='subprocess'] == ['--check','--admit']*2


def test_batch_stops_on_failed_audit(fw, monkeypatch):
    _, ext, _, _ = fw
    visited=[]
    async def captured(label, slot): visited.append(slot)
    monkeypatch.setattr(ext,'capture',captured)
    monkeypatch.setattr(ext.subprocess,'run',lambda *args,**kwargs: SimpleNamespace(returncode=2))
    with pytest.raises(RuntimeError, match='independent --check failed'):
        ext.batch('DOWNSTREAM_TIMEOUT', 1, 3, db_ack=False)
    assert visited == [1]


def test_emit_artifact_hashes_and_resource_samples(fw, monkeypatch):
    base, ext, _, root = fw
    names = tuple(f'feature_{i}' for i in range(65))
    datasets = ModuleType('datasets'); datasets.__path__=[]
    final = ModuleType('datasets.final_features'); final.FEATURE_NAMES = names
    jsonl = ModuleType('scripts.phase19c2c_healthy_pilot')
    jsonl.jsonl_bytes = lambda rows: b''.join(base.serial(r) for r in rows)
    monkeypatch.setitem(sys.modules,'datasets',datasets)
    monkeypatch.setitem(sys.modules,'datasets.final_features',final)
    monkeypatch.setitem(sys.modules,'scripts.phase19c2c_healthy_pilot',jsonl)
    runid = 'v2train-'+'c'*16
    path = ext.create_intent('DATABASE_LOCK_CONTENTION', 1, runid)
    now=datetime.now(UTC)
    window=SimpleNamespace(started_at=now,ended_at=now+timedelta(seconds=60))
    ext.emit(path, runid, 'DATABASE_LOCK_CONTENTION','train',
             requests=[{'request_id':'r'}],recovery=[{'request_id':'r2'}],events=[{'event_type':'http'}],
             window=window,vector=dict.fromkeys(names,0.0),metadata={'test':True},
             truth={'fault_id':'DATABASE_LOCK_CONTENTION'},db_samples=[{'advisory_waiters':2}])
    manifest=json.loads((path/'source_manifest.json').read_text())
    assert 'db_samples.jsonl' in manifest['artifacts']
    assert set(manifest['artifacts']) == {f.name for f in path.iterdir()}-{'intent.json','source_manifest.json'}
    rec=manifest['artifacts']['db_samples.jsonl']
    assert rec['sha256'] == base.digest((path/'db_samples.jsonl').read_bytes())
    base.release_own_lock(runid)
    with pytest.raises(FileExistsError):
        ext.emit(path,runid,'DATABASE_LOCK_CONTENTION','train',requests=[],recovery=[],events=[],
                 window=window,vector=dict.fromkeys(names,0.0),metadata={},truth={},db_samples=[{}])


def test_auditor_rejects_absent_evidence(fw):
    base, ext, audit, root = fw
    with pytest.raises(ValueError, match='invalid capture directory'):
        audit.audit('SERVICE_UNAVAILABLE', 1)


def test_auditor_rejects_symlink(fw):
    base, ext, audit, root = fw
    base.RUNS.mkdir(parents=True)
    link = base.RUNS / 'service_unavailable-01'
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError as exc:
        if sys.platform == 'win32' and getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows denies creation of a directory symlink without privileges')
        raise
    with pytest.raises(ValueError, match='invalid capture directory'):
        audit.audit('SERVICE_UNAVAILABLE', 1)


def test_capture_failure_preserves_reservation(fw, monkeypatch):
    base, ext, audit, root = fw
    monkeypatch.setattr(base,'preflight', lambda: None)
    async def fail(*args): raise RuntimeError('simulated fault evidence failure')
    monkeypatch.setattr(ext,'observations',fail)
    with pytest.raises(RuntimeError,match='simulated'):
        asyncio.run(ext.capture('SERVICE_UNAVAILABLE',1))
    assert base.LOCK.is_dir()
    assert ext.slot_path('SERVICE_UNAVAILABLE',1).joinpath('intent.json').is_file()
    assert not ext.slot_path('SERVICE_UNAVAILABLE',1).joinpath('admission_receipt.json').exists()

@pytest.mark.parametrize('label',[
    'DOWNSTREAM_TIMEOUT','DATABASE_HIGH_LATENCY','SERVICE_UNAVAILABLE',
    'CONNECTION_POOL_EXHAUSTION','DATABASE_LOCK_CONTENTION',
])
def test_auditor_round_trip_and_tamper_fails(fw, monkeypatch, label):
    """Fake telemetry checks exact manifest/receipt wiring; actual effect algorithms stay unchanged."""
    base, ext, aud, root=fw
    names=tuple(f'feature_{i}' for i in range(65))
    datasets=ModuleType('datasets'); datasets.__path__=[]
    feats=ModuleType('datasets.final_features'); feats.FEATURE_NAMES=names
    windows=ModuleType('datasets.phase19c2b_exact_windows')
    windowbox={}
    windows.select_exact_window=lambda **kwargs: windowbox['window']
    windows.build_observable_features=lambda **kwargs: dict.fromkeys(names,0.)
    good=ModuleType('scripts.phase19c2c_healthy_pilot')
    good.jsonl_bytes=lambda rows: b''.join((json.dumps(r,sort_keys=True)+'\n').encode() for r in rows)
    fault=ModuleType('scripts.phase19c2d_fault_pilot')
    fault.parsed_time=lambda x: datetime.fromisoformat(x.replace('Z','+00:00')) if isinstance(x,str) else x
    fault.correlation_gate=lambda *args: None
    fault.recovery_gate=lambda *args,**kwargs: {'passed':8}
    impact={'fault_id':label,'affected':80}
    timeout=ModuleType('scripts.phase19c2j_timeout_pilot')
    timeout.timeout_impact=lambda *args: impact
    service=ModuleType('scripts.phase19c2m_service_effect_pilot')
    service.observed_impact=lambda *args: impact
    resource=ModuleType('scripts.phase19c2n_resource_pilot')
    resource.effect_gate=lambda *args: impact
    for k,v in [('datasets',datasets),('datasets.final_features',feats),
                ('datasets.phase19c2b_exact_windows',windows),
                ('scripts.phase19c2c_healthy_pilot',good),
                ('scripts.phase19c2d_fault_pilot',fault),
                ('scripts.phase19c2j_timeout_pilot',timeout),
                ('scripts.phase19c2m_service_effect_pilot',service),
                ('scripts.phase19c2n_resource_pilot',resource)]:
        monkeypatch.setitem(sys.modules,k,v)
    target,intensity,mechanism=ext.spec(label)
    rid='v2train-'+'d'*16
    path=ext.create_intent(label,1,rid)
    started=datetime.now(UTC)+timedelta(seconds=5)
    ended=started+timedelta(seconds=75)
    watermark=ended
    window=SimpleNamespace(started_at=ended-timedelta(seconds=62),
                           ended_at=ended-timedelta(seconds=2))
    windowbox['window']=window
    req=[{'run_id':rid,'request_id':rid+f'-request-{i:05d}', 'trace_id':f'trace-{i}',
          'scenario':'valid','started_at':(started+timedelta(seconds=i*0.66)).isoformat()}
         for i in range(112)]
    rec=[{'run_id':rid,'request_id':rid+f'-request-{i:05d}', 'trace_id':f'trace-{i}',
          'scenario':'valid','started_at':(ended+timedelta(seconds=2)).isoformat()}
         for i in range(113,121)]
    events=[{'run_id':rid,'timestamp':(started+timedelta(seconds=0.67*i)).isoformat(),
             'request_id':req[i]['request_id'],'trace_id':req[i]['trace_id']}
            for i in range(112)]
    events.append({'run_id':rid,'timestamp':watermark.isoformat(),'request_id':rec[0]['request_id'],
                   'trace_id':rec[0]['trace_id']})
    activated=started-timedelta(seconds=2)
    cleared=ended+timedelta(seconds=1)
    expire=ended+timedelta(seconds=65)
    meta={'phase_started_at':started.isoformat(),'phase_ended_at':ended.isoformat(),
          'telemetry_watermark':watermark.isoformat(),
          'fault_activated_at':activated.isoformat(),
          'fault_expires_at':expire.isoformat(),'fault_cleared_at':cleared.isoformat(),
          'healthy_pre_post':True,'scheduled_requests':112,'scheduled_rps':1.5,
          'impact':impact,'recovery':{'passed':8},'mechanism':mechanism}
    samples=None
    if mechanism=='resource':
        meta['preflight']={'fault':label,'remaining_after_allowance':50,
                           'baseline_connections':24,'baseline_waiters':0}
        samples=[{'timestamp':started.isoformat(),'total_connections':40,'advisory_waiters':12}]
    truth={'run_id':rid,'fault_id':label,'target':target,'intensity':intensity,
           'activated_at':activated.isoformat(),'expires_at':expire.isoformat(),
           'cleared_at':cleared.isoformat(),'truth_role':'audit_only_not_model_feature'}
    ext.emit(path,rid,label,'train',requests=req,recovery=rec,events=events,
             window=window,vector=dict.fromkeys(names,0.),metadata=meta,truth=truth,
             db_samples=samples)
    base.release_own_lock(rid)
    accepted=aud.audit(label,1)
    assert accepted['verdict']=='VALID_V2_TRAINING_RUN'
    assert not (path/'admission_receipt.json').exists()
    accepted=aud.audit(label,1,write=True)
    assert (path/'admission_receipt.json').is_file()
    with (path/'events.jsonl').open('ab') as f: f.write(b'{"tampered":true}\n')
    with pytest.raises(ValueError,match='Invalid or missing observation|digest mismatch'):
        aud.audit(label,1)
