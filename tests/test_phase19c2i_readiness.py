"""Offline safeguards: no Docker, network, model, or generation of dataset rows."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'phase19c2i_readiness.py'
spec = importlib.util.spec_from_file_location('phase19c2i_test_module', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def make_plans(monkeypatch):
    rows=[]
    for k in range(60):
        split='train' if k%20 <12 else 'validation' if k%20 <16 else 'test'
        rows.append(dict(run_id=f'normal-{k}', split=split, fault_id=None, target=None,
                         target_rps=None, execution_allowed=False, training_eligible=False,
                         threshold_tuning_eligible=False, window_seconds=60, capture_seconds=75))
    for family in m.FAMILY_REQUIREMENTS:
        for k in range(20):
            split='train' if k<12 else 'validation' if k<16 else 'test'
            target=('transaction->payment' if k%2 == 0 else 'payment->ledger') if family=='DOWNSTREAM_TIMEOUT' else 'payment'
            rows.append(dict(run_id=f'{family}-{k}', split=split, fault_id=family, target=target,
                             target_rps=None, execution_allowed=False, training_eligible=False,
                             threshold_tuning_eligible=False, window_seconds=60, capture_seconds=75))
    parent={'runs':copy.deepcopy(rows),'status':'DRAFT_BLOCKED_NOT_APPROVED'}
    parent_digest=m.canonical_digest(parent)
    parent['draft_sha256']=parent_digest
    proposal={'runs':copy.deepcopy(rows),'status':'DRAFT_BLOCKED_NOT_APPROVED',
              'source_draft_sha256':parent_digest,'target_changes':[{'id':k} for k in range(10)],
              'reachability_only_not_fault_effect_proof':True,
              'approval':{'rate_envelope':False,'fault_exposure_per_target':False,
                          'infrastructure_disruption':False,'training_eligibility':False,
                          'fresh_unknown_holdout':False,'bulk_execution':False}}
    prop_digest=m.canonical_digest(proposal)
    proposal['draft_sha256']=prop_digest
    monkeypatch.setattr(m,'PARENT_SHA',parent_digest)
    monkeypatch.setattr(m,'PROPOSAL_SHA',prop_digest)
    return parent,proposal


def make_pilot(tmp_path, *, rate=2.0, count=120, manifest_schema='19c2c.pilot.1', fault=False):
    directory=tmp_path/('v2fault-afd5cd1cfbc44c65' if fault else 'v2pilot-01f0974b69ce4220')
    directory.mkdir()
    start=datetime(2026,10,11,tzinfo=timezone.utc)
    features={f'feature_{i}':0.0 for i in range(65)}
    features.update({'request_count':float(count),'request_rate_per_second':float(rate),
                     'request_success_rate':1.0,'event_error_rate':0.0,'event_failure_rate':0.0,
                     'event_count':3000.0,'request_latency_p50_ms':200.0,
                     'request_latency_p95_ms':300.0,'request_latency_p99_ms':500.0})
    # Replaced keys above were not present; retain exactly 65 by constructing separate names.
    special={'request_count','request_rate_per_second','request_success_rate',
             'event_error_rate','event_failure_rate','event_count',
             'request_latency_p50_ms','request_latency_p95_ms','request_latency_p99_ms'}
    features={key:features[key] for key in special} | {f'feature_{i}':0.0 for i in range(65-len(special))}
    candidate=dict(run_id=directory.name,split='diagnostic_only',training_eligible=False,
                   threshold_tuning_eligible=False,label=None,window_seconds=60,feature_count=65,
                   window_started_at=start.isoformat(),window_ended_at=(start+timedelta(seconds=60)).isoformat(),
                   candidate_status='UNLABELED_FAULT_PILOT' if fault else 'UNLABELED_HEALTHY_PILOT',features=features)
    names=['requests.jsonl','events.jsonl','candidate_feature.json']
    if fault:
        names += ['recovery_requests.jsonl','protected_ground_truth.json']
    payloads={n:b'{}\n' for n in names}
    payloads['candidate_feature.json']=(json.dumps(candidate)+'\n').encode()
    if fault:
        payloads['protected_ground_truth.json']=(json.dumps({'run_id':directory.name,
                   'fault_id':'DOWNSTREAM_ERROR_BURST','target':'payment','intensity':'medium'})+'\n').encode()
    manifest=dict(schema_version='19c2d.pilot.1' if fault else manifest_schema,
                  run_id=directory.name,split='diagnostic_only',training_eligible=False,
                  threshold_tuning_eligible=False,
                  metadata={'impact':{'in_window_requests':120,'affected_client_requests':74,
                         'correlated_payment_500_events':74},
                         'recovery':{'successful_requests':8,'same_run_id':True,'started_after_fault_clear':True}},
                  artifacts={n:{'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)} for n,raw in payloads.items()})
    for name,raw in payloads.items():
        (directory/name).write_bytes(raw)
    (directory/'manifest.json').write_text(json.dumps(manifest))
    return directory


def test_valid_blocked_matrix(monkeypatch):
    parent,proposal=make_plans(monkeypatch)
    result=m.verify_blocked_matrix(parent,proposal)
    assert result['blocked']==240 and result['known_fault']==180

@pytest.mark.parametrize('field,value',[
    ('execution_allowed',True),('training_eligible',True),
    ('threshold_tuning_eligible',True),('target_rps',1.5),
    ('window_seconds',59),('capture_seconds',60)
])
def test_unsafe_row_rejected(monkeypatch,field,value):
    parent,proposal=make_plans(monkeypatch)
    proposal['runs'][0][field]=value
    monkeypatch.setattr(m,'PROPOSAL_SHA',m.canonical_digest(proposal))
    proposal['draft_sha256']=m.canonical_digest(proposal)
    with pytest.raises(ValueError,match='blocked-run'):
        m.verify_blocked_matrix(parent,proposal)


def test_invalid_digest_rejected(monkeypatch):
    parent,proposal=make_plans(monkeypatch)
    proposal['purpose']='changed without digest'
    with pytest.raises(ValueError,match='digest'):
        m.verify_blocked_matrix(parent,proposal)


def test_duplicate_run_id_rejected(monkeypatch):
    parent,proposal=make_plans(monkeypatch)
    proposal['runs'][1]['run_id']=proposal['runs'][0]['run_id']
    proposal['draft_sha256']=m.canonical_digest(proposal)
    monkeypatch.setattr(m,'PROPOSAL_SHA',proposal['draft_sha256'])
    with pytest.raises(ValueError,match='duplicate'):
        m.verify_blocked_matrix(parent,proposal)


def test_missing_family_rows_rejected(monkeypatch):
    parent,proposal=make_plans(monkeypatch)
    proposal['runs'][-1]['fault_id']='DOWNSTREAM_TIMEOUT'
    proposal['draft_sha256']=m.canonical_digest(proposal)
    monkeypatch.setattr(m,'PROPOSAL_SHA',proposal['draft_sha256'])
    with pytest.raises(ValueError,match='nine known fault'):
        m.verify_blocked_matrix(parent,proposal)


def test_positive_approval_rejected(monkeypatch):
    parent,proposal=make_plans(monkeypatch)
    proposal['approval']['bulk_execution']=True
    proposal['draft_sha256']=m.canonical_digest(proposal)
    monkeypatch.setattr(m,'PROPOSAL_SHA',proposal['draft_sha256'])
    with pytest.raises(ValueError,match='unauthorized approval'):
        m.verify_blocked_matrix(parent,proposal)


def test_unbalanced_timeout_targets_rejected(monkeypatch):
    parent,proposal=make_plans(monkeypatch)
    row=next(r for r in proposal['runs'] if r['fault_id']=='DOWNSTREAM_TIMEOUT')
    row['target']='payment->ledger'
    proposal['draft_sha256']=m.canonical_digest(proposal)
    monkeypatch.setattr(m,'PROPOSAL_SHA',proposal['draft_sha256'])
    with pytest.raises(ValueError,match='timeout path balance'):
        m.verify_blocked_matrix(parent,proposal)


def test_healthy_reference(tmp_path):
    pilot=make_pilot(tmp_path)
    result=m.healthy_reference(pilot,2.0,pilot.name,'19c2c.pilot.1',120)
    assert result['latency_p95_ms']==300 and result['verified_artifact_hashes']==3


def test_tampered_artifact_rejected(tmp_path):
    pilot=make_pilot(tmp_path)
    (pilot/'events.jsonl').write_text('tampered\n')
    with pytest.raises(ValueError,match='checksum'):
        m.healthy_reference(pilot,2.0,pilot.name,'19c2c.pilot.1',120)


def test_wrong_rate_rejected(tmp_path):
    pilot=make_pilot(tmp_path)
    with pytest.raises(ValueError,match='saved reference traffic'):
        m.healthy_reference(pilot,2.5,pilot.name,'19c2c.pilot.1',150)


def test_pilot_label_leakage_rejected(tmp_path):
    pilot=make_pilot(tmp_path)
    candidate=json.loads((pilot/'candidate_feature.json').read_text())
    candidate['label']='NORMAL'
    raw=(json.dumps(candidate)+'\n').encode()
    (pilot/'candidate_feature.json').write_bytes(raw)
    manifest=json.loads((pilot/'manifest.json').read_text())
    manifest['artifacts']['candidate_feature.json']={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    (pilot/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='invalid diagnostic'):
        m.healthy_reference(pilot,2.0,pilot.name,'19c2c.pilot.1',120)


def test_fault_reference(tmp_path):
    pilot=make_pilot(tmp_path, fault=True)
    result=m.fault_reference(pilot)
    assert result['correlated_5xx']==74 and result['post_clear_successful']==8


def test_fault_provenance_rejected(tmp_path):
    pilot=make_pilot(tmp_path,fault=True)
    manifest=json.loads((pilot/'manifest.json').read_text())
    manifest['metadata']['impact']['correlated_payment_500_events']=0
    (pilot/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='expected observed fault impact'):
        m.fault_reference(pilot)


def test_report_never_authorizes_training():
    matrix=dict(runs=240,normal=60,known_fault=180,blocked=240)
    pilots=[{'scheduled_rps':r} for r in (1.5,2.0,2.5)]
    report=m.readiness_report(matrix,pilots,{'run_id':'v2fault-test'},'a'*64)
    assert report['status']=='NO_GO_BULK_COLLECTION'
    assert all(v is False for v in report['approval'].values())
    assert len(report['per_family'])==9
    assert all(v['training_rows_authorized']==0 for v in report['per_family'].values())


def test_missing_reference_rate_rejected():
    with pytest.raises(ValueError,match='missing required'):
        m.readiness_report({},[{'scheduled_rps':1.5}],{'run_id':'v2fault-test'},'x')


def test_non_exact_window_rejected():
    assert m.seconds('2026-10-11T09:00:00Z','2026-10-11T09:01:00+00:00')==60
    with pytest.raises(ValueError,match='timezone'):
        m.seconds('2026-10-11T09:00:00','2026-10-11T09:01:00')
