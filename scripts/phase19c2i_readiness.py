#!/usr/bin/env python3
"""Phase 19C.2I: audit *blocked* v2 collection readiness using immutable local evidence.

--audit is read-only, --write creates only a separate offline report.
No Docker/network/traffic/fault activation/model training or approval is implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PARENT = Path('data/datasets/v2/plan/phase19c2e-draft-matrix.json')
PROPOSAL = Path('data/datasets/v2/plan/phase19c2g-reachability-proposal.json')
PILOT_15 = Path('data/datasets/v2/pilot/v2pilot-ff5edf80e69444c4')
PILOT_20 = Path('data/datasets/v2/pilot/v2pilot-01f0974b69ce4220')
PILOT_25 = Path('data/datasets/v2/calibration/v2cal-f7507fd18e9a4daa')
FAULT = Path('data/datasets/v2/pilot-fault/v2fault-afd5cd1cfbc44c65')
REPORT = Path('data/datasets/v2/plan/phase19c2i-readiness-audit.json')
PARENT_SHA = '9eed555e72e378a8c7e7d6dc3e3d3724a716fce3b96dafc72d841fd0403567b5'
PROPOSAL_SHA = '577b8e877466aae9df96c161ad911e0278cadaa50cf879dd89110d635c755cd9'
SCHEMA = '19c2i.readiness-audit.1'

FAMILY_REQUIREMENTS = {
    'AUTHENTICATION_FAILURE_BURST': ('burst_profile', 'prove rejected-login burst at bounded rate and recovery'),
    'CONNECTION_POOL_EXHAUSTION': ('database_pool', 'prove bounded pool saturation, release, and DB recovery'),
    'DATABASE_HIGH_LATENCY': ('database_delay', 'prove DB delay in target service on verified model window'),
    'DATABASE_LOCK_CONTENTION': ('database_lock', 'prove lock waits, bounded lease, and successful unlock'),
    'DATABASE_UNAVAILABLE': ('infrastructure_outage', 'require explicit approval for PostgreSQL interruption and recovery'),
    'DOWNSTREAM_ERROR_BURST': ('service_error', 'prove errors and recovery separately for each planned target/intensity'),
    'DOWNSTREAM_TIMEOUT': ('edge_timeout', 'prove timeout for each observed target edge and recovery'),
    'HIGH_LOAD': ('overload_calibration', 'establish safe load and early-abort resource monitoring'),
    'SERVICE_UNAVAILABLE': ('service_outage', 'require safe service-level outage authorization and recovery'),
}


def strict_json(path: Path) -> dict[str, Any]:
    def reject_constant(x: str) -> None:
        raise ValueError('invalid JSON constant: ' + x)
    obj = json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)
    if not isinstance(obj, dict):
        raise ValueError(f'{path}: expected a JSON object')
    return obj


def canonical_digest(obj: dict[str, Any]) -> str:
    copy = dict(obj)
    copy.pop('draft_sha256', None)
    return hashlib.sha256(json.dumps(copy, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')).hexdigest()


def verify_blocked_matrix(parent: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    """Pure, conservative checks in addition to 19C.2G's full deterministic re-derivation."""
    if parent.get('draft_sha256') != PARENT_SHA or canonical_digest(parent) != PARENT_SHA:
        raise ValueError('parent draft provenance mismatch')
    if proposal.get('draft_sha256') != PROPOSAL_SHA or canonical_digest(proposal) != PROPOSAL_SHA:
        raise ValueError('19C.2G proposal digest mismatch')
    if proposal.get('source_draft_sha256') != PARENT_SHA or proposal.get('status') != 'DRAFT_BLOCKED_NOT_APPROVED':
        raise ValueError('proposal is not the expected blocked derivative')
    rows = proposal.get('runs')
    if not isinstance(rows, list) or len(rows) != 240:
        raise ValueError('expected exactly 240 rows')
    if len({r.get('run_id') for r in rows if isinstance(r, dict)}) != 240:
        raise ValueError('invalid or duplicate run identities')
    if Counter(r.get('split') for r in rows) != {'train':144,'validation':48,'test':48}:
        raise ValueError('split counts unexpectedly changed')
    classes = Counter(r.get('fault_id') for r in rows if r.get('fault_id') is not None)
    if classes != {name: 20 for name in FAMILY_REQUIREMENTS}:
        raise ValueError('nine known fault families are missing or imbalanced')
    if sum(r.get('fault_id') is None for r in rows) != 60:
        raise ValueError('healthy control count changed')
    if any(r.get('execution_allowed') is not False or r.get('training_eligible') is not False
           or r.get('threshold_tuning_eligible') is not False or r.get('target_rps') is not None
           or r.get('window_seconds') != 60 or r.get('capture_seconds') != 75 for r in rows):
        raise ValueError('blocked-run safety contract changed')
    approval = proposal.get('approval')
    if not isinstance(approval, dict) or set(approval) != {
            'rate_envelope', 'fault_exposure_per_target', 'infrastructure_disruption',
            'training_eligibility', 'fresh_unknown_holdout', 'bulk_execution'}:
        raise ValueError('missing or unexpected approval fields')
    if any(value is not False for value in approval.values()):
        raise ValueError('unauthorized approval present')
    if len(proposal.get('target_changes', [])) != 10:
        raise ValueError('10-row path-correction evidence missing')
    if proposal.get('reachability_only_not_fault_effect_proof') is not True:
        raise ValueError('reachability evidence misrepresented as fault effect')
    timeout_counts = Counter(r['target'] for r in rows if r['fault_id'] == 'DOWNSTREAM_TIMEOUT')
    if timeout_counts != {'transaction->payment':10, 'payment->ledger':10}:
        raise ValueError('timeout path balance invalid')
    return {'runs': 240, 'normal': 60, 'known_fault':180,
            'splits':dict(Counter(r['split'] for r in rows)), 'per_known_class':dict(sorted(classes.items())),
            'timeout_targets':dict(sorted(timeout_counts.items())), 'blocked':240}


def seconds(a: str, b: str) -> float:
    start = datetime.fromisoformat(str(a).replace('Z','+00:00'))
    end = datetime.fromisoformat(str(b).replace('Z','+00:00'))
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError('timezone required in evidence window')
    return (end-start).total_seconds()


def verified_digest_artifacts(directory: Path, expected: tuple[str,...]) -> tuple[dict,dict]:
    manifest = strict_json(directory/'manifest.json')
    if manifest.get('run_id') != directory.name or manifest.get('split') != 'diagnostic_only':
        raise ValueError(f'pilot identity or diagnostic split mismatch: {directory}')
    if manifest.get('training_eligible') is not False or manifest.get('threshold_tuning_eligible') is not False:
        raise ValueError(f'pilot unexpectedly training-eligible: {directory}')
    entries = manifest.get('artifacts')
    if not isinstance(entries, dict) or set(entries) != set(expected):
        raise ValueError(f'pilot artifact inventory mismatch: {directory}')
    for name in expected:
        entry = entries[name]
        raw = (directory/name).read_bytes()
        if entry.get('sha256') != hashlib.sha256(raw).hexdigest() or entry.get('bytes') != len(raw):
            raise ValueError(f'pilot checksum/size mismatch: {directory.name}/{name}')
    candidate = strict_json(directory/'candidate_feature.json')
    if (candidate.get('run_id') != directory.name or candidate.get('split') != 'diagnostic_only'
        or candidate.get('training_eligible') is not False or candidate.get('threshold_tuning_eligible') is not False
        or candidate.get('label') is not None or candidate.get('window_seconds') != 60
        or candidate.get('feature_count') != 65):
        raise ValueError(f'invalid diagnostic feature-vector metadata: {directory}')
    if not math.isclose(seconds(candidate['window_started_at'],candidate['window_ended_at']),60.0,abs_tol=1e-6):
        raise ValueError('non-exact pilot feature window')
    features = candidate.get('features')
    if not isinstance(features,dict) or len(features)!=65 or any(type(v) not in (float,int) or not math.isfinite(v) for v in features.values()):
        raise ValueError('invalid feature vector')
    return manifest, candidate


def healthy_reference(directory: Path, expected_rate:float, expected_id:str, schema:str, expected_n:int) -> dict[str, Any]:
    if directory.name != expected_id:
        raise ValueError('unexpected pilot ID')
    manifest,candidate = verified_digest_artifacts(directory,('requests.jsonl','events.jsonl','candidate_feature.json'))
    if manifest.get('schema_version') != schema:
        raise ValueError('unexpected pilot manifest schema')
    f = candidate['features']
    if (abs(f.get('request_rate_per_second',-100)-expected_rate)>1e-6
        or f.get('request_count') != expected_n or f.get('request_success_rate') != 1.0
        or f.get('event_error_rate') != 0.0 or f.get('event_failure_rate') != 0.0):
        raise ValueError('saved reference traffic/success contract unexpectedly differs')
    return {'run_id':expected_id, 'scheduled_rps':expected_rate, 'in_window_requests':expected_n,
            'latency_p50_ms':f['request_latency_p50_ms'],'latency_p95_ms':f['request_latency_p95_ms'],
            'latency_p99_ms':f['request_latency_p99_ms'], 'event_count':f['event_count'],
            'success_rate':f['request_success_rate'],'event_failure_rate':f['event_failure_rate'],
            'verified_artifact_hashes':3, 'status':'SINGLE_DIAGNOSTIC_REFERENCE_ONLY'}


def fault_reference(directory:Path) -> dict[str,Any]:
    if directory.name != 'v2fault-afd5cd1cfbc44c65':
        raise ValueError('unexpected fault pilot ID')
    names=('requests.jsonl','recovery_requests.jsonl','events.jsonl','candidate_feature.json','protected_ground_truth.json')
    manifest, candidate = verified_digest_artifacts(directory,names)
    truth = strict_json(directory/'protected_ground_truth.json')
    meta = manifest.get('metadata') or {}
    impact = meta.get('impact') or {}
    recovery = meta.get('recovery') or {}
    if (manifest.get('schema_version')!='19c2d.pilot.1' or truth.get('fault_id')!='DOWNSTREAM_ERROR_BURST'
        or truth.get('target')!='payment' or truth.get('intensity')!='medium'
        or truth.get('run_id')!=directory.name or candidate.get('candidate_status')!='UNLABELED_FAULT_PILOT'):
        raise ValueError('fault pilot provenance or contract mismatch')
    if (impact.get('in_window_requests') != 120 or impact.get('affected_client_requests')!=74
        or impact.get('correlated_payment_500_events')!=74
        or recovery.get('successful_requests')!=8 or recovery.get('same_run_id') is not True
        or recovery.get('started_after_fault_clear') is not True):
        raise ValueError('expected observed fault impact/recovery record changed')
    return {'run_id':directory.name, 'fault_id':truth['fault_id'],'target':truth['target'],
            'intensity':truth['intensity'],'in_window_requests':120,
            'affected_client_requests':74,'correlated_5xx':74,'post_clear_successful':8,
            'verified_artifact_hashes':5,'status':'ONE_DIAGNOSTIC_TARGET_INTENSITY_ONLY'}


def readiness_report(matrix:dict, healthy:list[dict], fault:dict, proposal_sha:str)->dict:
    if len(healthy)!=3 or {p['scheduled_rps'] for p in healthy}!={1.5,2.0,2.5}:
        raise ValueError('missing required diagnostic rates')
    # No automatic approval: 1 trial per healthy rate, 1 fault/target/intensity.
    per_family = {}
    for fault_id, (kind, explanation) in FAMILY_REQUIREMENTS.items():
        per_family[fault_id] = {
            'planned_runs':20, 'gate_kind':kind, 'status':'BLOCKED_NEEDS_CLASS_TARGET_PROOF',
            'required_next_evidence':explanation,
            'existing_diagnostic_reference': fault['run_id'] if fault_id=='DOWNSTREAM_ERROR_BURST' else None,
            'training_rows_authorized':0,
        }
    report = {
        'schema_version':SCHEMA,
        'status':'NO_GO_BULK_COLLECTION',
        'project':'FaultWeave',
        'plan_sha256':proposal_sha,
        'plan':matrix,
        'calibration':{
            'healthy_pilots':healthy,
            'provisional_research_candidates_rps':{'low':1.5,'nominal':2.0,'elevated':None},
            '2p5_rps':'DIAGNOSTIC_ONLY_NOT_APPROVED',
            'healthy_single_run_limit':'No statistically established safe upper rate; p99 spike at 2.0 RPS',
            'approval':False,
        },
        'known_fault_diagnostic':fault,
        'per_family':per_family,
        'remaining_blockers':[
            'healthy rate envelope needs repeated/resource-monitored evidence, not one trial per rate',
            'all nine families need bounded target/intensity-specific activation and recovery gates',
            'higher-throughput traffic families must have separate validated rates',
            'infrastructure outage requires explicit approval and safe isolation',
            'all eligible known-fault runs need exact fault-only 60s windows and independent feature parity',
            'retain run-level train/validation/test isolation and true unknown holdout with no calibration leakage',
            'original known-fault pilot and all healthy pilots remain diagnostic-only',
        ],
        'approval':{
            'bulk_execution':False,'training_eligibility':False,'rate_envelope':False,
            'per_target_fault_exposure':False,'fresh_unknown_holdout':False,
        },
        'report_role':'read-only evidence summary; cannot be consumed as execution authorization',
    }
    return report


def audit(root:Path)->dict:
    root=root.resolve(strict=True)
    from scripts.phase19c2g_reconcile import build
    parent,expected,_coverage=build(root,root/PARENT,root/PILOT_20)
    saved=strict_json(root/PROPOSAL)
    if saved != expected:
        raise ValueError('saved 19C.2G proposal does not match independently derived correction')
    matrix=verify_blocked_matrix(parent,saved)
    references=[
        healthy_reference(root/PILOT_15,1.5,'v2pilot-ff5edf80e69444c4','19c2c.pilot.1',90),
        healthy_reference(root/PILOT_20,2.0,'v2pilot-01f0974b69ce4220','19c2c.pilot.1',120),
        healthy_reference(root/PILOT_25,2.5,'v2cal-f7507fd18e9a4daa','19c2h.calibration.1',150),
    ]
    fault=fault_reference(root/FAULT)
    return readiness_report(matrix,references,fault,saved['draft_sha256'])


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    choices=p.add_mutually_exclusive_group(required=True)
    choices.add_argument('--audit',action='store_true',help='read-only full readiness audit')
    choices.add_argument('--write',action='store_true',help='create-only separate NON-executable audit report')
    args=p.parse_args(argv)
    try:
        report=audit(ROOT)
        print('PHASE 19C.2I — V2 COLLECTION READINESS (OFFLINE)')
        print('PARENT & PROPOSAL: full 19C.2G provenance reproduced; 240/240 runs blocked')
        print('HEALTHY DIAGNOSTICS: 1.5, 2.0, 2.5 RPS; 3/3 artifact hashes per pilot verified')
        for item in report['calibration']['healthy_pilots']:
            print('  RPS',item['scheduled_rps'], 'requests',item['in_window_requests'],
                  'p95',item['latency_p95_ms'],'ms','p99',item['latency_p99_ms'],'ms')
        print('FAULT DIAGNOSTIC: payment DOWNSTREAM_ERROR_BURST, 74/120 impacted; 8/8 recovered; hashes 5/5')
        print('FAMILY BLOCKERS:',len(report['per_family']),'of 9; no class-wide fault-effect approval')
        print('TRAINING-ELIGIBLE:',0,'EXECUTABLE:',0)
        print('VERDICT: NO_GO_BULK_COLLECTION — this is the correct safety outcome')
        if args.write:
            destination=ROOT/REPORT
            destination.parent.mkdir(parents=True,exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError('will not overwrite existing report: '+str(destination))
            with destination.open('x',encoding='utf-8',newline='\n') as fh:
                json.dump(report,fh,indent=2,sort_keys=True,allow_nan=False)
                fh.write('\n')
            print('CREATE-ONLY REPORT:',destination)
        else:
            print('READ-ONLY: no files written')
        return 0
    except (ValueError,KeyError,TypeError,ImportError,OSError,RuntimeError) as exc:
        print('STOP:',exc,file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
