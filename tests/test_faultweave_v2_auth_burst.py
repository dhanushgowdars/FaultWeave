"""Offline checks for guarded authentication burst capture and independent admission."""
from __future__ import annotations
import asyncio
import importlib
import json
import shutil
import sys
from datetime import UTC,datetime,timedelta
from pathlib import Path
from types import ModuleType,SimpleNamespace
import pytest


def fixture_modules(tmp_path,monkeypatch):
    root=tmp_path/'workspace'; pkg=root/'scripts';pkg.mkdir(parents=True)
    (pkg/'__init__.py').write_text('')
    for filename in ('faultweave_v2_capture.py','faultweave_v2_independent_audit.py',
                     'faultweave_v2_verified_capture.py','faultweave_v2_auth_burst.py',
                     'faultweave_v2_auth_burst_audit.py'):
        source=Path(__file__).resolve().parents[1]/'scripts'/filename
        shutil.copy2(source,pkg/filename)
    monkeypatch.syspath_prepend(str(root))
    for name in list(sys.modules):
        if name=='scripts' or name.startswith('scripts.faultweave_v2_'):
            monkeypatch.delitem(sys.modules,name,raising=False)
    base=importlib.import_module('scripts.faultweave_v2_capture')
    auth=importlib.import_module('scripts.faultweave_v2_auth_burst')
    audit=importlib.import_module('scripts.faultweave_v2_auth_burst_audit')
    base.ROOT=root;base.RUNS=root/'data/datasets/v2/training-captures'
    base.CONTROL=root/'data/datasets/v2/control';base.LOCK=base.CONTROL/'v2-training-active'
    base.SUPERVISOR_LOCK=base.CONTROL/'phase19c2k-active'
    monkeypatch.setattr(base,'ensure_compact_parent',lambda:None)
    return root,base,auth,audit

@pytest.fixture
def fw(tmp_path,monkeypatch): return fixture_modules(tmp_path,monkeypatch)


def test_slot_split_and_reject_invalid(fw):
    _,_,auth,_=fw
    assert [auth.split_for(i) for i in (1,8,9,10,11,12)]==['train','train','validation','validation','test','test']
    for slot in (-1,0,13,True,1.5):
        with pytest.raises(ValueError): auth.split_for(slot)


def test_preview_does_not_mutate(fw,capsys):
    root,_,auth,_=fw
    auth.main(['--preview','--start-slot','1','--count','1'])
    assert not (root/'data').exists()
    assert '20-60RPS' in capsys.readouterr().out


def test_requires_explicit_ack(fw):
    _,_,auth,_=fw
    with pytest.raises(ValueError,match='acknowledgment'):
        auth.main(['--batch','--start-slot','1','--count','1'])


def test_auth_burst_evidence_requires_measured_rate_and_traces(fw,monkeypatch):
    _,_,auth,_=fw
    mod=ModuleType('scripts.phase19c2d_fault_pilot')
    mod.parsed_time=lambda x:datetime.fromisoformat(x) if isinstance(x,str) else x
    monkeypatch.setitem(sys.modules,'scripts.phase19c2d_fault_pilot',mod)
    begin=datetime.now(UTC);window=SimpleNamespace(started_at=begin,ended_at=begin+timedelta(seconds=60))
    requests=[{'request_id':f'x{i}','trace_id':f't{i}','started_at':(begin+timedelta(seconds=(i+1)/5)).isoformat(),
               'scenario':'invalid_login','status_code':401,'expected_outcome':True,'transport_error':None}
              for i in range(299)]
    events=[{'request_id':r['request_id'],'trace_id':r['trace_id'],
             'timestamp':r['started_at'],'service':'authentication','event_type':'http_request_completed',
             'status_code':401} for r in requests]
    good=auth.effect_gate(window,requests,events)
    assert good['client_401_rejections']==299
    assert good['correlated_authentication_401_requests']==299
    assert good['original_20_60_rps_band_proven'] is False
    with pytest.raises(RuntimeError,match='AUTH_BURST_NOT_PROVEN'):
        auth.effect_gate(window,requests[:100],events[:100])
    with pytest.raises(RuntimeError,match='trace-matched'):
        auth.effect_gate(window,requests,events[:100])
    failing=[dict(r) for r in requests]
    for r in failing[100:]:r['status_code']=200
    with pytest.raises(RuntimeError,match='high-frequency'):
        auth.effect_gate(window,failing,events)


def test_intent_guarded_and_duplicate_slot(fw):
    _,base,auth,_=fw
    runid='v2train-'+'a'*16
    path=auth.make_intent(1,runid)
    intent=json.loads((path/'intent.json').read_text())
    assert intent['rps']==5.0 and intent['collector']==auth.SCHEMA_COLLECTOR
    with pytest.raises(RuntimeError,match='reservation'):
        auth.make_intent(2,'v2train-'+'b'*16)
    base.release_own_lock(runid)
    with pytest.raises(RuntimeError,match='previously attempted'):
        auth.make_intent(1,runid)


def test_audit_roundtrip_and_tamper(fw,monkeypatch):
    root,base,auth,audit=fw
    names=tuple('feature_%02d'%i for i in range(65))
    datasets=ModuleType('datasets');datasets.__path__=[]
    features=ModuleType('datasets.final_features');features.FEATURE_NAMES=names
    windows=ModuleType('datasets.phase19c2b_exact_windows')
    box={}
    windows.select_exact_window=lambda **kwargs:box['window']
    windows.build_observable_features=lambda **kwargs:dict.fromkeys(names,0.0)
    scriptsgood=ModuleType('scripts.phase19c2c_healthy_pilot')
    scriptsgood.jsonl_bytes=lambda items:b''.join((json.dumps(x,sort_keys=True)+'\n').encode() for x in items)
    fault=ModuleType('scripts.phase19c2d_fault_pilot')
    fault.parsed_time=lambda x:datetime.fromisoformat(x.replace('Z','+00:00')) if isinstance(x,str) else x
    fault.correlation_gate=lambda *args:None
    fault.recovery_gate=lambda *args,**kwargs:{'passed':8}
    for name,mod in [('datasets',datasets),('datasets.final_features',features),
                     ('datasets.phase19c2b_exact_windows',windows),
                     ('scripts.phase19c2c_healthy_pilot',scriptsgood),
                     ('scripts.phase19c2d_fault_pilot',fault)]:
        monkeypatch.setitem(sys.modules,name,mod)
    rid='v2train-'+'c'*16
    folder=auth.make_intent(1,rid)
    start=datetime.now(UTC)+timedelta(seconds=5)
    end=start+timedelta(seconds=75)
    watermark=end
    window=SimpleNamespace(started_at=end-timedelta(seconds=62),ended_at=end-timedelta(seconds=2))
    box['window']=window
    req=[{'run_id':rid,'request_id':rid+f'-request-{i:05d}',
          'trace_id':f'trace-{i}', 'scenario':'invalid_login','status_code':401,
          'expected_outcome':True,'transport_error':None,
          'started_at':(start+timedelta(seconds=i/5)).isoformat()} for i in range(375)]
    recovery=[{'run_id':rid,'request_id':rid+f'-request-{i:05d}',
               'trace_id':f'trace-{i}','scenario':'valid',
               'started_at':(end+timedelta(seconds=1)).isoformat()} for i in range(375,383)]
    events=[{'run_id':rid,'request_id':r['request_id'],'trace_id':r['trace_id'],
             'timestamp':r['started_at'],'service':'authentication',
             'event_type':'http_request_completed','status_code':401} for r in req]
    events.append({'run_id':rid,'request_id':recovery[0]['request_id'],
                   'trace_id':recovery[0]['trace_id'],'timestamp':watermark.isoformat()})
    active=start-timedelta(seconds=2)
    cleared=end+timedelta(seconds=1)
    expiry=end+timedelta(seconds=45)
    impact=auth.effect_gate(window,req,events)
    metadata={'phase_started_at':start.isoformat(),'phase_ended_at':end.isoformat(),
              'telemetry_watermark':watermark.isoformat(),
              'fault_activated_at':active.isoformat(),
              'fault_expires_at':expiry.isoformat(),
              'fault_cleared_at':cleared.isoformat(),
              'healthy_pre_post':True,'scheduled_requests':375,'scheduled_rps':5.,
              'impact':impact,'recovery':{'passed':8},'mechanism':'auth-rejection-burst'}
    truth={'run_id':rid,'fault_id':auth.LABEL,'target':auth.TARGET,
           'intensity':auth.INTENSITY,'activated_at':active.isoformat(),
           'expires_at':expiry.isoformat(),'cleared_at':cleared.isoformat(),
           'truth_role':'audit_only_not_model_feature'}
    from scripts import faultweave_v2_verified_capture as five
    five.emit(folder,rid,auth.LABEL,'train',requests=req,recovery=recovery,events=events,
              window=window,vector=dict.fromkeys(names,0.0),
              metadata=metadata,truth=truth,db_samples=None)
    base.release_own_lock(rid)
    assert audit.audit(1)['verdict']=='VALID_V2_TRAINING_RUN'
    assert not (folder/'admission_receipt.json').exists()
    audit.audit(1,write=True)
    assert (folder/'admission_receipt.json').exists()
    with (folder/'events.jsonl').open('ab') as out:out.write(b'{"tampered":true}\n')
    with pytest.raises(ValueError,match='digest|observation'):
        audit.audit(1)


def test_failed_capture_keeps_journal(fw,monkeypatch):
    _,base,auth,_=fw
    monkeypatch.setattr(base,'preflight',lambda:None)
    async def broken(*args):raise RuntimeError('synthetic failure')
    monkeypatch.setattr(auth,'observe',broken)
    with pytest.raises(RuntimeError,match='synthetic'):
        asyncio.run(auth.capture(1))
    assert auth.directory_for(1).joinpath('intent.json').exists()
    assert base.LOCK.is_dir()
