"""Offline tests for non-executable Phase 19C.2G correction. Never starts Docker."""
from __future__ import annotations

import copy
import json
from collections import Counter

import pytest

from scripts.phase19c2e_matrix import make_draft
from scripts.phase19c2f_path_audit import canonical_digest, assess_matrix
from scripts.phase19c2g_reconcile import (
    ELIGIBLE_TIMEOUT_EDGES, PARENT_DIGEST, prepare, verify_proposal, main,
)


@pytest.fixture
def parent():
    value = make_draft()
    assert value['draft_sha256'] == PARENT_DIGEST
    return value


@pytest.fixture
def coverage():
    return {
        'edge_request_counts': {
            'gateway->account': 150,
            'gateway->authentication': 150,
            'gateway->transaction': 150,
            'transaction->account': 150,
            'transaction->ledger': 150,
            'transaction->payment': 150,
            'payment->ledger': 150,
        },
        'service_request_counts': {name: 150 for name in ('gateway','authentication','transaction','payment','account','ledger')},
        'edge_event_counts': {},
    }


def test_original_sha(parent):
    assert parent['draft_sha256'] == PARENT_DIGEST


def test_exact_10_changes_and_only_target(parent, coverage):
    result = prepare(parent, coverage, 'v2pilot-reference')
    assert len(result['target_changes']) == 10
    changed = {r['run_id'] for r in result['target_changes']}
    for before, after in zip(parent['runs'], result['runs']):
        if before['run_id'] in changed:
            assert {**after, 'target':before['target']} == before
        else:
            assert after == before


def test_only_timeout_class_modified(parent, coverage):
    result = prepare(parent, coverage, 'v2pilot-reference')
    assert all(x['fault_id']=='DOWNSTREAM_TIMEOUT' for x in result['target_changes'])


def test_split_balance(parent, coverage):
    result = prepare(parent, coverage, 'v2pilot-reference')
    for split, n in (('train',6),('validation',2),('test',2)):
        c=Counter(row['target'] for row in result['runs'] if row['fault_id']=='DOWNSTREAM_TIMEOUT' and row['split']==split)
        assert c=={edge:n for edge in ELIGIBLE_TIMEOUT_EDGES}


def test_original_unmodified(parent, coverage):
    frozen=copy.deepcopy(parent)
    prepare(parent, coverage, 'v2pilot-reference')
    assert parent == frozen


def test_proposal_deterministic(parent, coverage):
    a=prepare(parent, coverage, 'v2pilot-reference')
    b=prepare(parent, coverage, 'v2pilot-reference')
    assert a==b and a['draft_sha256']==canonical_digest(a)


def test_all_rows_blocked(parent,coverage):
    r=prepare(parent, coverage, 'v2pilot-reference')
    assert all(row['execution_allowed'] is False and row['training_eligible'] is False
               and row['threshold_tuning_eligible'] is False and row['target_rps'] is None for row in r['runs'])
    assert r['calibration']['nominal_rps'] is None
    assert all(v is False for v in r['approval'].values())


def test_counts_unchanged(parent,coverage):
    r=prepare(parent,coverage,'v2pilot-reference')
    assert Counter(x['split'] for x in r['runs'])=={'train':144,'validation':48,'test':48}
    assert Counter(x['scenario_type'] for x in r['runs'])=={'normal':60,'known_fault':180}


def test_no_unreachable_issues(parent,coverage):
    r=prepare(parent,coverage,'v2pilot-reference')
    assert not assess_matrix(r['runs'],coverage)['issues']


def test_reject_weak_replacement_edge(parent,coverage):
    coverage['edge_request_counts']['transaction->payment']=1
    with pytest.raises(ValueError,match='insufficient'):
        prepare(parent,coverage,'v2pilot-reference')


def test_reject_unanticipated_missing_service(parent,coverage):
    coverage['service_request_counts']['payment']=0
    with pytest.raises(ValueError,match='expected exactly 10'):
        prepare(parent,coverage,'v2pilot-reference')


def test_reject_original_parent_sha(parent,coverage):
    parent['draft_sha256']='bad'
    with pytest.raises(ValueError,match='parent SHA'):
        prepare(parent,coverage,'v2pilot-reference')


def test_reject_modified_intensity(parent,coverage):
    p=prepare(parent,coverage,'v2pilot-reference')
    p['runs'][0]['intensity']='high'
    p['draft_sha256']=canonical_digest(p)
    with pytest.raises(ValueError,match='unexplained'):
        verify_proposal(parent,p,coverage,'v2pilot-reference')


def test_reject_unexplained_execution_enable(parent,coverage):
    p=prepare(parent,coverage,'v2pilot-reference')
    p['runs'][0]['execution_allowed']=True
    p['draft_sha256']=canonical_digest(p)
    with pytest.raises(ValueError):
        verify_proposal(parent,p,coverage,'v2pilot-reference')


def test_reject_false_fault_effect_proof(parent,coverage):
    p=prepare(parent,coverage,'v2pilot-reference')
    p['reachability_only_not_fault_effect_proof']=False
    p['draft_sha256']=canonical_digest(p)
    with pytest.raises(ValueError,match='must NOT'):
        verify_proposal(parent,p,coverage,'v2pilot-reference')


def test_reject_unlogged_change(parent,coverage):
    p=prepare(parent,coverage,'v2pilot-reference')
    p['target_changes']=p['target_changes'][:-1]
    p['draft_sha256']=canonical_digest(p)
    with pytest.raises(ValueError,match='incomplete'):
        verify_proposal(parent,p,coverage,'v2pilot-reference')


def test_reject_approval_change(parent,coverage):
    p=prepare(parent,coverage,'v2pilot-reference')
    p['approval']['bulk_execution']=True
    p['draft_sha256']=canonical_digest(p)
    with pytest.raises(ValueError,match='approval'):
        verify_proposal(parent,p,coverage,'v2pilot-reference')


def test_reject_wrong_reference(parent,coverage):
    p=prepare(parent,coverage,'v2pilot-reference')
    with pytest.raises(ValueError,match='reference'):
        verify_proposal(parent,p,coverage,'different-reference')


def test_reject_tampered_checksum(parent,coverage):
    p=prepare(parent,coverage,'v2pilot-reference')
    p['draft_sha256']='0'*64
    with pytest.raises(ValueError,match='checksum'):
        verify_proposal(parent,p,coverage,'v2pilot-reference')


def test_reject_tampered_parent_metadata(parent, coverage):
    p = prepare(parent, coverage, 'v2pilot-reference')
    p['calibration']['low_rps'] = 4.0
    p['draft_sha256'] = canonical_digest(p)
    with pytest.raises(ValueError, match='metadata'):
        verify_proposal(parent, p, coverage, 'v2pilot-reference')


def test_reject_fabricated_reference_edge_count(parent, coverage):
    p=prepare(parent,coverage,'v2pilot-reference')
    p['reference_edge_distinct_requests']['payment->ledger']=1000
    p['draft_sha256']=canonical_digest(p)
    with pytest.raises(ValueError,match='provenance'):
        verify_proposal(parent,p,coverage,'v2pilot-reference')


def test_cli_create_and_verify(tmp_path, parent, capsys):
    import hashlib
    root=tmp_path
    plan=root/'data/datasets/v2/plan/phase19c2e-draft-matrix.json'
    plan.parent.mkdir(parents=True,exist_ok=True)
    plan.write_text(json.dumps(parent),encoding='utf-8')
    pilot=root/'data/datasets/v2/pilot/v2pilot-synthetic-fixture'
    pilot.mkdir(parents=True)
    run_id='v2pilot-synthetic-fixture'
    reqs=[]; events=[]
    edges = [('gateway','account'),('gateway','authentication'),('gateway','transaction'),
             ('transaction','account'),('transaction','ledger'),('transaction','payment'),('payment','ledger')]
    for i in range(20):
        req=f'test-{i}'; trace=f'trace-{i}'
        reqs.append({'run_id':run_id,'request_id':req,'trace_id':trace,
                     'scenario':'valid','status_code':200,'expected_outcome':True})
        for service in ('gateway','authentication','transaction','payment','account','ledger'):
            events.append({'run_id':run_id,'request_id':req,'trace_id':trace,'service':service,'event_type':'http_request_completed'})
        for service,target in edges:
            events.append({'run_id':run_id,'request_id':req,'trace_id':trace,'service':service,
                           'downstream_service':target,'event_type':'downstream_request_completed'})
    artifacts={}
    for name, value in [('requests.jsonl',reqs),('events.jsonl',events),('candidate_feature.json',None)]:
        out=pilot/name
        out.write_text('{}\n' if value is None else ''.join(json.dumps(v)+'\n' for v in value),encoding='utf-8')
        raw=out.read_bytes()
        artifacts[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    (pilot/'manifest.json').write_text(json.dumps({'run_id':run_id,'split':'diagnostic_only',
        'training_eligible':False,'threshold_tuning_eligible':False,'artifacts':artifacts}),encoding='utf-8')
    argv=['--project-root',str(root),'--healthy-pilot',str(pilot)]
    assert main([*argv, '--dry-run']) == 0
    proposed=root/'data/datasets/v2/plan/phase19c2g-reachability-proposal.json'
    assert not proposed.exists()
    assert main([*argv,'--write']) == 0
    assert proposed.exists()
    assert main([*argv,'--verify']) == 0
    assert main([*argv,'--write']) == 2
    assert json.loads(plan.read_text()) == parent
    saved=json.loads(proposed.read_text())
    assert len(saved['target_changes'])==10
    saved['runs'][0]['execution_allowed']=True
    proposed.write_text(json.dumps(saved))
    assert main([*argv,'--verify']) == 2
