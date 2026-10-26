"""Offline no-Docker tests for 19C.2F research path audit."""
import hashlib
import json
from pathlib import Path

import pytest
from scripts.phase19c2f_path_audit import (
    assess_matrix, audit, canonical_digest, extract_coverage, load_verified_pilot,
    read_draft,
)


def ev(req='r1', svc='transaction', target='payment', kind='downstream_request_completed'):
    return {'run_id':'v2pilot-example', 'request_id':req,'trace_id':f't-{req}',
            'service':svc,'downstream_service':target,'event_type':kind}


def row(rid='1', fault='DOWNSTREAM_TIMEOUT', target='transaction->payment', split='train'):
    return {'run_id':rid,'fault_id':fault,'target':target,'split':split}


def fixture(root: Path, covered=True):
    d = root / 'data/datasets/v2/plan/phase19c2e-draft-matrix.json'
    d.parent.mkdir(parents=True,exist_ok=True)
    rows=[]
    for i in range(240):
        r=row(str(i), 'DOWNSTREAM_TIMEOUT', 'transaction->payment' if covered else 'payment->account')
        r.update({'execution_allowed':False,'training_eligible':False,
                  'threshold_tuning_eligible':False,'target_rps':None})
        rows.append(r)
    plan={'status':'DRAFT_BLOCKED_NOT_APPROVED','runs':rows}
    plan['draft_sha256']=canonical_digest(plan)
    d.write_text(json.dumps(plan))
    p=root / 'data/datasets/v2/pilot/v2pilot-example'
    p.mkdir(parents=True)
    requests=[];events=[]
    for i in range(20):
        r=f'r{i}'
        requests.append({'run_id':'v2pilot-example', 'request_id':r, 'trace_id':f't-{r}',
                         'scenario':'valid','status_code':200,'expected_outcome':True})
        events.append(ev(r,'transaction','payment'))
        events.append(ev(r,'gateway',None,'http_request_completed'))
    contents = {'requests.jsonl': requests,'events.jsonl':events}
    manifest={'run_id':'v2pilot-example','training_eligible':False,
              'threshold_tuning_eligible':False,'split':'diagnostic_only','artifacts':{}}
    for name,records in contents.items():
        b=''.join(json.dumps(x)+'\n' for x in records).encode()
        (p/name).write_bytes(b)
        manifest['artifacts'][name]={'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}
    b=b'{}\n'
    (p/'candidate_feature.json').write_bytes(b)
    manifest['artifacts']['candidate_feature.json']={'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}
    (p/'manifest.json').write_text(json.dumps(manifest))
    return d,p


def test_canonical_digest_excludes_digest():
    d={'status':'DRAFT_BLOCKED_NOT_APPROVED','runs':[]}; old=canonical_digest(d)
    d['draft_sha256']='irrelevant'
    assert canonical_digest(d)==old


def test_extract_calls_only_and_unique_requests():
    c=extract_coverage([ev(),ev(), ev(kind='other'),ev('r2','payment','ledger', 'downstream_request_failed')])
    assert c['edge_request_counts']=={'payment->ledger':1,'transaction->payment':1}
    assert c['edge_event_counts']['transaction->payment']==2


def test_missing_transfer_only_edge_detected():
    c=extract_coverage([ev(f'r{i}') for i in range(20)])
    f=assess_matrix([row('1','DOWNSTREAM_TIMEOUT','payment->account')],c)
    assert len(f['issues'])==1
    assert f['issues'][0]['reason']=='TARGET_EDGE_NOT_OBSERVED'


def test_present_dependency_still_not_fault_proof():
    c=extract_coverage([ev(f'r{i}') for i in range(20)])
    f=assess_matrix([row()],c)
    assert f['issues']==[]
    assert f['status_counts']=={'EDGE_OBSERVED_FAULT_EFFECT_UNPROVEN':1}
    assert not f['fault_exposure_approved']


def test_insufficient_unique_requests_not_enough():
    c=extract_coverage([ev('r1') for _ in range(100)])
    f=assess_matrix([row()],c)
    assert len(f['issues'])==1


def test_infra_fault_never_auto_approved():
    c=extract_coverage([ev(f'r{i}') for i in range(20)])
    f=assess_matrix([row('1','DATABASE_UNAVAILABLE','postgresql')],c)
    assert f['status_counts']=={'INFRASTRUCTURE_OUTAGE_REQUIRES_EXPLICIT_APPROVAL':1}
    assert not f['rate_envelope_approved']


def test_services_are_not_edges():
    c=extract_coverage([ev(f'r{i}','payment',None,'http_request_completed') for i in range(20)])
    f=assess_matrix([row('1','SERVICE_UNAVAILABLE','payment')],c)
    assert f['issues']==[]
    f2=assess_matrix([row('1','DOWNSTREAM_TIMEOUT','payment->account')],c)
    assert len(f2['issues'])==1


def test_good_fixture_is_read_only(tmp_path):
    d,p=fixture(tmp_path)
    b1,b2=d.read_bytes(),(p/'manifest.json').read_bytes()
    result=audit(tmp_path,d,p)
    assert len(result['findings']['issues'])==0
    assert result['healthy_reference_run_id']=='v2pilot-example'
    assert d.read_bytes()==b1 and (p/'manifest.json').read_bytes()==b2


def test_uncovered_fixture_no_go(tmp_path):
    d,p=fixture(tmp_path,covered=False)
    assert len(audit(tmp_path,d,p)['findings']['issues'])==240


def test_tampered_reference_fails(tmp_path):
    d,p=fixture(tmp_path)
    with (p/'events.jsonl').open('a') as f: f.write('{}\n')
    with pytest.raises(ValueError,match='integrity'):
        audit(tmp_path,d,p)


def test_tampered_draft_fails(tmp_path):
    d,p=fixture(tmp_path)
    info=json.loads(d.read_text());info['runs'][0]['target_rps']=2.0
    d.write_text(json.dumps(info))
    with pytest.raises(ValueError,match='checksum'):
        audit(tmp_path,d,p)


def test_correct_digest_but_unsafe_row_rejected(tmp_path):
    d,p=fixture(tmp_path)
    info=json.loads(d.read_text());info['runs'][0]['execution_allowed']=True
    info['draft_sha256']=canonical_digest(info);d.write_text(json.dumps(info))
    with pytest.raises(ValueError,match='unauthorized'):
        read_draft(d)


def test_cross_run_trace_fails(tmp_path):
    d,p=fixture(tmp_path)
    m=json.loads((p/'manifest.json').read_text())
    events=json.loads((p/'events.jsonl').read_text().splitlines()[0]);events['trace_id']='BAD'
    lines=(p/'events.jsonl').read_text().splitlines();lines[0]=json.dumps(events)
    raw=('\n'.join(lines)+'\n').encode();(p/'events.jsonl').write_bytes(raw)
    m['artifacts']['events.jsonl']={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    (p/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='mismatched event trace'):
        load_verified_pilot(p)


def test_schema_rows_240():
    x=[row(str(i),'SERVICE_UNAVAILABLE','payment') for i in range(240)]
    assert assess_matrix(x,extract_coverage([ev('r1','payment',None)]))['assessed_runs']==240


def test_min_unique_reject_zero():
    with pytest.raises(ValueError):
        assess_matrix([],extract_coverage([]),min_unique_requests=0)
