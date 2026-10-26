"""Offline safety/compatibility tests for Phase 19C.2K.

Do not require Docker, a live gateway, or an existing fault-control lease.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import phase19c2k_collection_controller as k


@pytest.mark.parametrize('kind,target,module,parent', [
    ('healthy',None,'scripts.phase19c2c_healthy_pilot','data/datasets/v2/pilot'),
    ('payment-error','payment','scripts.phase19c2d_fault_pilot','data/datasets/v2/pilot-fault'),
    ('timeout','payment->ledger','scripts.phase19c2j_timeout_pilot','data/datasets/v2/pilot-fault'),
    ('timeout','transaction->payment','scripts.phase19c2j_timeout_pilot','data/datasets/v2/pilot-fault'),
])
def test_verified_adapter_mapping(kind,target,module,parent):
    item=k.choose_adapter(kind,target,1.5)
    assert (item.module,item.relative_dir,item.target,item.rps)==(module,parent,target,1.5)
    assert item.command(execute=False)[1:3]==['-m',module]
    assert '--dry-run' in item.command(execute=False)
    assert '--execute' in item.command(execute=True)
    assert '--execute' not in item.command(execute=False)


@pytest.mark.parametrize('kind,target,rps', [
    ('timeout','payment->account',1.5),('timeout','ledger->account',1.5),
    ('timeout',None,1.5),('payment-error','ledger',1.5),
    ('healthy','payment',1.5),('DATABASE_UNAVAILABLE','postgresql',1.5),
    ('HIGH_LOAD','gateway',1.5),('timeout','payment->ledger',2.5),
    ('timeout','payment->ledger',float('nan')),
    ('timeout','payment->ledger',float('inf')),
    ('healthy',None,1.0),('healthy',None,True),
])
def test_unapproved_adapter_refused(kind,target,rps):
    with pytest.raises(ValueError):k.choose_adapter(kind,target,rps)


def test_existing_plan_still_240_blocked_and_balanced():
    from scripts.phase19c2i_readiness import PROPOSAL,strict_json
    plan=strict_json(k.ROOT/PROPOSAL)
    rows=plan['runs']
    assert len(rows)==240
    assert len({r['run_id'] for r in rows})==240
    assert all(r['execution_allowed'] is False and r['training_eligible'] is False
               and r['threshold_tuning_eligible'] is False and r['target_rps'] is None
               for r in rows)
    assert Counter(r['split'] for r in rows)=={'train':144,'validation':48,'test':48}
    assert Counter(r['fault_id'] for r in rows if r['fault_id'])=={
        name:20 for name in (
            'DATABASE_HIGH_LATENCY','DATABASE_UNAVAILABLE','SERVICE_UNAVAILABLE',
            'DOWNSTREAM_TIMEOUT','AUTHENTICATION_FAILURE_BURST','HIGH_LOAD',
            'CONNECTION_POOL_EXHAUSTION','DATABASE_LOCK_CONTENTION','DOWNSTREAM_ERROR_BURST'
        )
    }
    assert all(v is False for v in plan['approval'].values())


def mocked_project(monkeypatch,tmp_path):
    row={'run_id':'v2-normal-low-01','execution_allowed':False,
         'training_eligible':False,'target_rps':None}
    doc={'runs':[dict(row) for _ in range(240)],
         'draft_sha256':'some-hash', 'approval':{'bulk_execution':False}}
    # Make IDs unique for the fake contract used in main tests.
    for i,r in enumerate(doc['runs']):r['run_id']=f'fake-{i:03d}'
    doc['runs'][0]['run_id']='v2-normal-low-01'
    monkeypatch.setattr(k,'ROOT',tmp_path)
    monkeypatch.setattr(k,'readiness_audit',lambda root:{
        'status':'NO_GO_BULK_COLLECTION',
        'plan_sha256':'some-hash',
        'plan':{'blocked':240},
        'approval':{'bulk_execution':False,'training_eligibility':False},
    })
    monkeypatch.setattr(k,'strict_json',lambda p:doc)
    return doc


def test_project_verification_denies_new_plan_permission(tmp_path,monkeypatch):
    doc=mocked_project(monkeypatch,tmp_path)
    assert len(k.verify_blocked_project(tmp_path)['runs'])==240
    doc['runs'][5]['execution_allowed']=True
    with pytest.raises(RuntimeError):k.verify_blocked_project(tmp_path)
    doc['runs'][5]['execution_allowed']=False
    doc['approval']['bulk_execution']=True
    with pytest.raises(RuntimeError):k.verify_blocked_project(tmp_path)


def test_project_verification_denies_training_and_missing_report(tmp_path,monkeypatch):
    doc=mocked_project(monkeypatch,tmp_path)
    doc['runs'][5]['training_eligible']=True
    with pytest.raises(RuntimeError):k.verify_blocked_project(tmp_path)
    doc['runs'][5]['training_eligible']=False
    monkeypatch.setattr(k,'readiness_audit',lambda root: {'status':'READY','plan_sha256':'some-hash','plan':{'blocked':240},'approval':{'bulk_execution':False,'training_eligibility':False}})
    with pytest.raises(RuntimeError):k.verify_blocked_project(tmp_path)


def test_inspect_plan_row_never_launches(tmp_path,monkeypatch,capsys):
    mocked_project(monkeypatch,tmp_path)
    monkeypatch.setattr(k.subprocess,'run',lambda *a,**kw:pytest.fail('Unexpected subprocess'))
    assert k.main(['--inspect-run','v2-normal-low-01'])==0
    assert 'BLOCKED PLAN ROW' in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_unknown_plan_row_fails_closed(tmp_path,monkeypatch):
    mocked_project(monkeypatch,tmp_path)
    assert k.main(['--inspect-run','unknown'])==2


def test_status_read_only(tmp_path,monkeypatch,capsys):
    mocked_project(monkeypatch,tmp_path)
    assert k.main(['--status'])==0
    assert '240/240 BLOCKED' in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_pilot_dry_run_no_docker_no_writes(tmp_path,monkeypatch,capsys):
    mocked_project(monkeypatch,tmp_path)
    monkeypatch.setattr(k.subprocess,'run',lambda *a,**kw:pytest.fail('Unexpected child'))
    assert k.main(['--pilot','--kind','timeout','--target','payment->ledger','--rps','1.5','--dry-run'])==0
    assert 'NEW UUID PILOT ONLY' in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_execute_requires_explicit_ack_and_unique_id(tmp_path,monkeypatch):
    mocked_project(monkeypatch,tmp_path)
    for args in [
        ['--pilot','--kind','healthy','--rps','1.5','--execute-pilot'],
        ['--pilot','--kind','healthy','--rps','1.5','--execute-pilot','--attempt-id','test-a'],
        ['--pilot','--kind','healthy','--rps','1.5','--dry-run','--acknowledge-diagnostic-only'],
    ]:
        assert k.main(args)==2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('attempt', ['xx','UPPER','v2fault-deadbeef12345678','../escape','a/bcd','hello space',''])
def test_attempt_id_rejected(tmp_path,attempt):
    with pytest.raises(ValueError):k.attempt_paths(tmp_path,attempt)


def test_existing_pilot_output_rejects_symlink(tmp_path):
    a=k.choose_adapter('timeout','payment->ledger',1.5)
    directory=tmp_path/a.relative_dir
    directory.mkdir(parents=True)
    try:
        (directory/'v2timeout-aaaaaaaaaaaaaaaa').symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('OS does not permit symlinks in an ordinary test process')
    with pytest.raises(RuntimeError):k.existing_pilots(tmp_path,a)


def test_existing_pilot_output_rejects_broken_names(tmp_path):
    a=k.choose_adapter('timeout','payment->ledger',1.5)
    directory=tmp_path/a.relative_dir
    directory.mkdir(parents=True)
    (directory/'v2timeout-evil').mkdir()
    with pytest.raises(RuntimeError):k.existing_pilots(tmp_path,a)


def test_result_journal_success_audited_once(tmp_path,monkeypatch):
    from scripts import phase19c2c_healthy_pilot as h
    monkeypatch.setattr(h,'require_clean_local_stack',lambda:None)
    adapter=k.choose_adapter('timeout','payment->ledger',1.5)
    targetdir=tmp_path/adapter.relative_dir
    targetdir.mkdir(parents=True)
    run_id='v2timeout-aaaaaaaaaaaaaaaa'
    called=[]
    def child(cmd,*,cwd,check):
        assert cmd==adapter.command(execute=True)
        assert cwd==tmp_path
        (targetdir/run_id).mkdir()
        (targetdir/run_id/'manifest.json').write_text('immutable mock manifest')
        called.append('child')
        return SimpleNamespace(returncode=0)
    def auditor(root,pilot,requested):
        assert pilot==targetdir/run_id and requested==adapter
        called.append('audit')
    monkeypatch.setattr(k.subprocess,'run',child)
    monkeypatch.setattr(k,'audit_pilot',auditor)
    contract={'proposal_sha256':'testdigest'}
    out=k.run_diagnostic(tmp_path,contract,adapter,'test-001')
    assert out==targetdir/run_id
    assert called==['child','audit']
    journal=json.loads((tmp_path/k.CONTROL/'test-001'/'result.json').read_text())
    assert journal['training_eligible'] is False
    assert journal['verdict']=='VALID_DIAGNOSTIC_ONLY'
    assert journal['run_id']==run_id
    assert journal['manifest_sha256']==hashlib.sha256(b'immutable mock manifest').hexdigest()
    assert not (tmp_path/k.LOCK).exists()
    with pytest.raises(RuntimeError,match='already exists'):
        k.run_diagnostic(tmp_path,contract,adapter,'test-001')
    assert called==['child','audit']


def test_child_failure_preserves_lock_and_never_retries(tmp_path,monkeypatch):
    from scripts import phase19c2c_healthy_pilot as h
    monkeypatch.setattr(h,'require_clean_local_stack',lambda:None)
    adapter=k.choose_adapter('healthy',None,1.5)
    monkeypatch.setattr(k.subprocess,'run',lambda *args,**kw: SimpleNamespace(returncode=9))
    with pytest.raises(RuntimeError,match='exited 9'):
        k.run_diagnostic(tmp_path,{'proposal_sha256':'sha'},adapter,'test-failed')
    assert (tmp_path/k.LOCK/'owner.json').is_file()
    assert (tmp_path/k.CONTROL/'test-failed'/'intent.json').is_file()
    assert not (tmp_path/k.CONTROL/'test-failed'/'result.json').exists()
    with pytest.raises(RuntimeError,match='lock exists'):
        k.run_diagnostic(tmp_path,{'proposal_sha256':'sha'},adapter,'test-next')


def test_unexpected_multiple_output_fails_closed(tmp_path,monkeypatch):
    from scripts import phase19c2c_healthy_pilot as h
    monkeypatch.setattr(h,'require_clean_local_stack',lambda:None)
    adapter=k.choose_adapter('healthy',None,1.5)
    target=tmp_path/adapter.relative_dir
    target.mkdir(parents=True)
    def child(*a,**kw):
        (target/'v2pilot-aaaaaaaaaaaaaaaa').mkdir()
        (target/'v2pilot-bbbbbbbbbbbbbbbb').mkdir()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(k.subprocess,'run',child)
    with pytest.raises(RuntimeError,match='exactly one'):
        k.run_diagnostic(tmp_path,{'proposal_sha256':'sha'},adapter,'test-002')
    assert (tmp_path/k.LOCK).exists()


def test_audit_failure_preserves_evidence_and_lock(tmp_path,monkeypatch):
    from scripts import phase19c2c_healthy_pilot as h
    monkeypatch.setattr(h,'require_clean_local_stack',lambda:None)
    adapter=k.choose_adapter('healthy',None,1.5)
    target=tmp_path/adapter.relative_dir
    target.mkdir(parents=True)
    def child(*a,**kw):
        (target/'v2pilot-aaaaaaaaaaaaaaaa').mkdir()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(k.subprocess,'run',child)
    monkeypatch.setattr(k,'audit_pilot',lambda *a: (_ for _ in ()).throw(ValueError('invalid parity')))
    with pytest.raises(ValueError,match='invalid parity'):
        k.run_diagnostic(tmp_path,{'proposal_sha256':'sha'},adapter,'test-003')
    assert (target/'v2pilot-aaaaaaaaaaaaaaaa').exists()
    assert (tmp_path/k.LOCK/'owner.json').is_file()
    assert not (tmp_path/k.CONTROL/'test-003'/'result.json').exists()


def test_status_counts_completed_and_incomplete(tmp_path):
    one=tmp_path/k.CONTROL/'abc-a'
    two=tmp_path/k.CONTROL/'abc-b'
    one.mkdir(parents=True)
    two.mkdir()
    (one/'result.json').write_text('{}')
    assert k.status_attempts(tmp_path)=={'completed':1,'incomplete':1}
