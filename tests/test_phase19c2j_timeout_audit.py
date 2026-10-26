"""Offline test for timeout saved-evidence audit; uses synthetic 65-feature fixture."""
from __future__ import annotations
import hashlib, json, sys, types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.phase19c2j_timeout_audit import audit, load_json, load_jsonl
from scripts.phase19c2d_fault_pilot import persist
import scripts.phase19c2d_fault_pilot as source

START=datetime(2026,10,11,10,0,tzinfo=UTC)
W=SimpleNamespace(started_at=START,ended_at=START+timedelta(seconds=60))
RUN='v2timeout-0123456789abcdef'
KEYS=tuple(f'f{i:02}' for i in range(65))
VEC={k:float(i) for i,k in enumerate(KEYS)}


def _fake_datasets(monkeypatch):
    ns=types.ModuleType('datasets');ns.__path__=[]
    feat=types.ModuleType('datasets.final_features');feat.FEATURE_NAMES=KEYS
    ex=types.ModuleType('datasets.phase19c2b_exact_windows')
    ex.select_exact_window=lambda **kwargs:W
    ex.build_observable_features=lambda **kwargs:dict(VEC)
    monkeypatch.setitem(sys.modules,'datasets',ns)
    monkeypatch.setitem(sys.modules,'datasets.final_features',feat)
    monkeypatch.setitem(sys.modules,'datasets.phase19c2b_exact_windows',ex)


def _data():
    req=[];recover=[];events=[]
    for i in range(1,101):
        rid=f'{RUN}-request-{i:05d}';trace=f'trace-{i}'
        at=START+timedelta(seconds=i/2)
        req.append({'run_id':RUN,'request_id':rid,'trace_id':trace,
                    'started_at':at.isoformat(),'latency_ms':6000.0,
                    'status_code':503,'expected_outcome':False,'transport_error':None,'scenario':'valid'})
        events.append({'run_id':RUN,'request_id':rid,'trace_id':trace,
                       'timestamp':(at+timedelta(seconds=1)).isoformat(),
                       'service':'payment','event_type':'downstream_request_failed',
                       'path':'/internal/v1/ledger/entries','downstream_service':'ledger',
                       'error_type':'ReadTimeout','status_code':None,
                       'latency_ms':6000.0,'success':False,'level':'ERROR'})
    for j in range(101,109):
        rid=f'{RUN}-request-{j:05d}';trace=f'trace-{j}'
        at=START+timedelta(seconds=90+(j-101))
        recover.append({'run_id':RUN,'request_id':rid,'trace_id':trace,
                        'started_at':at.isoformat(),'latency_ms':100.0,
                        'status_code':200,'expected_outcome':True,'transport_error':None,'scenario':'valid'})
        events.append({'run_id':RUN,'request_id':rid,'trace_id':trace,
                       'timestamp':(at+timedelta(milliseconds=100)).isoformat(),
                       'service':'gateway','event_type':'http_request_completed',
                       'path':'/api/v1/transactions','downstream_service':None,
                       'error_type':None,'status_code':200,
                       'latency_ms':100.0,'success':True,'level':'INFO'})
    return req,recover,events


def fixture(tmp_path,monkeypatch):
    _fake_datasets(monkeypatch)
    monkeypatch.setattr(source,'OUT_DIR',tmp_path/'data/datasets/v2/pilot-fault')
    requests,recovery,events=_data()
    impact={'in_window_requests':100,'affected_client_requests':100,
            'correlated_source_target_timeouts':100,'timeouts_with_affected_clients':100,
            'edge':'payment->ledger'}
    truth={'schema_version':'19c2j.timeout.1','run_id':RUN,
           'fault_id':'DOWNSTREAM_TIMEOUT','target':'payment->ledger','intensity':'medium',
           'activated_at':(START-timedelta(seconds=10)).isoformat(),
           'expires_at':(START+timedelta(seconds=105)).isoformat(),
           'cleared_at':(START+timedelta(seconds=85)).isoformat()}
    metadata={'observed_requests':len(requests),'observed_events':len(events),
              'source_script':'scripts/phase19c2j_timeout_pilot.py',
              'passed_pre_post_stack_health':True,'phase_started_at':(START-timedelta(seconds=5)).isoformat(),
              'phase_ended_at':(START+timedelta(seconds=75)).isoformat(),
              'telemetry_watermark':(START+timedelta(seconds=98)).isoformat(),
              'impact':impact,
              'recovery':{'successful_requests':8,'same_run_id':True,'started_after_fault_clear':True}}
    out=persist(RUN,requests,recovery,events,W,VEC,truth,metadata)
    return out


def test_full_independent_audit_fixture(tmp_path,monkeypatch,capsys):
    p=fixture(tmp_path,monkeypatch)
    assert audit(tmp_path,p)==0
    assert '65/65 identical' in capsys.readouterr().out


def test_tampered_artifact_rejected(tmp_path,monkeypatch):
    p=fixture(tmp_path,monkeypatch)
    path=p/'events.jsonl'
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='Checksum'):
        audit(tmp_path,p)


def test_invalid_json_rejected(tmp_path):
    p=tmp_path/'invalid.json';p.write_text('{"x":NaN}')
    with pytest.raises(ValueError):load_json(p)


def test_invalid_jsonl_rejected(tmp_path):
    p=tmp_path/'bad.jsonl';p.write_text('[]\n')
    with pytest.raises(ValueError):load_jsonl(p)
