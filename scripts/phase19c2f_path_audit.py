#!/usr/bin/env python3
"""FaultWeave 19C.2F: read-only workload/target coverage audit for the BLOCKED v2 draft.

Never runs experiments, enables draft rows, or changes original data.
Exits 2 on uncovered/weak targets; that is a useful NO-GO result, not a crash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRAFT = Path('data/datasets/v2/plan/phase19c2e-draft-matrix.json')
DEFAULT_PILOT = Path('data/datasets/v2/pilot/v2pilot-01f0974b69ce4220')
CALL_EVENT_TYPES = {'downstream_request_completed', 'downstream_request_failed'}
RATE_UNCALIBRATED = 'RATE_AND_FAULT_EXPOSURE_NOT_APPROVED'


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(value: dict[str, Any]) -> str:
    data = dict(value)
    data.pop('draft_sha256', None)
    return sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode())


def read_draft(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(plan, dict) or plan.get('status') != 'DRAFT_BLOCKED_NOT_APPROVED':
        raise ValueError('unexpected or executable draft status')
    if plan.get('draft_sha256') != canonical_digest(plan):
        raise ValueError('draft checksum mismatch')
    rows = plan.get('runs')
    if not isinstance(rows, list) or len(rows) != 240:
        raise ValueError('draft must contain 240 planned runs')
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid matrix row')
        rid = row.get('run_id')
        if not isinstance(rid, str) or rid in ids:
            raise ValueError('duplicate/bad matrix run ID')
        ids.add(rid)
        if (row.get('execution_allowed') is not False
                or row.get('training_eligible') is not False
                or row.get('threshold_tuning_eligible') is not False
                or row.get('target_rps') is not None):
            raise ValueError('unauthorized executable or training-eligible draft row')
        if row.get('split') not in ('train', 'validation', 'test'):
            raise ValueError('unknown draft split')
    return plan


def load_verified_pilot(directory: Path) -> tuple[str, list[dict], list[dict]]:
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(manifest, dict):
        raise ValueError('invalid pilot manifest')
    if (manifest.get('training_eligible') is not False
            or manifest.get('threshold_tuning_eligible') is not False
            or manifest.get('split') != 'diagnostic_only'):
        raise ValueError('pilot not safely diagnostic-only')
    run_id = manifest.get('run_id')
    if not isinstance(run_id, str) or not run_id.startswith('v2pilot-'):
        raise ValueError('bad healthy-pilot run ID')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, dict):
        raise ValueError('missing pilot checksum manifest')
    for name in ('requests.jsonl', 'events.jsonl', 'candidate_feature.json'):
        info = artifacts.get(name)
        if not isinstance(info, dict) or not isinstance(info.get('sha256'), str):
            raise ValueError(f'missing checksum for {name}')
        raw = (directory / name).read_bytes()
        if sha256(raw) != info['sha256'] or len(raw) != info.get('bytes'):
            raise ValueError(f'pilot integrity mismatch: {name}')
    requests = parse_jsonl(directory / 'requests.jsonl')
    events = parse_jsonl(directory / 'events.jsonl')
    if not requests or not events:
        raise ValueError('missing correlated pilot observations')
    seen: dict[str, str] = {}
    for req in requests:
        rid, trace = req.get('request_id'), req.get('trace_id')
        if req.get('run_id') != run_id or not isinstance(rid, str) or not trace or rid in seen:
            raise ValueError('bad/mixed/duplicate client request correlation')
        if req.get('scenario') != 'valid' or req.get('status_code') != 200 or req.get('expected_outcome') is not True:
            raise ValueError('reference pilot is not all-valid successful research traffic')
        seen[rid] = str(trace)
    correlated: set[str] = set()
    for event in events:
        if event.get('run_id') != run_id:
            raise ValueError('mixed-run event')
        rid = event.get('request_id')
        if rid not in seen:
            raise ValueError('unmatched request in pilot event evidence')
        if str(event.get('trace_id')) != seen[rid]:
            raise ValueError('mismatched event trace ID')
        correlated.add(rid)
    if len(correlated) != len(requests):
        raise ValueError('pilot has requests without supporting events')
    return run_id, requests, events


def parse_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f'non-object JSONL row {path.name}:{line_number}')
            rows.append(obj)
    return rows


def extract_coverage(events: list[dict]) -> dict[str, Any]:
    """Evidence of a dependency call: correlate a completed/failed HTTP client event."""
    edges: dict[str, set[str]] = defaultdict(set)
    services: dict[str, set[str]] = defaultdict(set)
    calls: Counter[str] = Counter()
    for event in events:
        service, req_id = event.get('service'), event.get('request_id')
        if not isinstance(service, str) or not isinstance(req_id, str):
            continue
        services[service].add(req_id)
        target = event.get('downstream_service')
        if (event.get('event_type') in CALL_EVENT_TYPES
                and isinstance(target, str) and target):
            edge = f'{service}->{target}'
            edges[edge].add(req_id)
            calls[edge] += 1
    return {
        'edge_request_counts': {k: len(v) for k, v in sorted(edges.items())},
        'edge_event_counts': dict(sorted(calls.items())),
        'service_request_counts': {k: len(v) for k, v in sorted(services.items())},
    }


def assess_matrix(rows: list[dict], coverage: dict, *, min_unique_requests: int = 10) -> dict:
    if min_unique_requests < 1:
        raise ValueError('coverage threshold must be positive')
    edges = coverage['edge_request_counts']
    services = coverage['service_request_counts']
    counts = Counter()
    issues = []
    per_fault = defaultdict(lambda: Counter())
    for row in rows:
        fault, target = row.get('fault_id'), row.get('target')
        split = row.get('split')
        if fault is None:
            status, count = 'HEALTHY_PATH_PRESENT', services.get('gateway', 0)
        elif fault in ('HIGH_LOAD', 'AUTHENTICATION_FAILURE_BURST'):
            status, count = 'TRAFFIC_MECHANISM_REQUIRES_SEPARATE_PROOF', services.get(str(target), 0)
        elif fault == 'DATABASE_UNAVAILABLE':
            status, count = 'INFRASTRUCTURE_OUTAGE_REQUIRES_EXPLICIT_APPROVAL', 0
        elif fault in ('DATABASE_LOCK_CONTENTION', 'CONNECTION_POOL_EXHAUSTION', 'DATABASE_HIGH_LATENCY'):
            status, count = 'SERVICE_OBSERVED_DB_MECHANISM_UNPROVEN', services.get(str(target), 0)
        elif fault in ('SERVICE_UNAVAILABLE', 'DOWNSTREAM_ERROR_BURST'):
            count = services.get(str(target), 0)
            status = 'SERVICE_OBSERVED_FAULT_EFFECT_UNPROVEN' if count >= min_unique_requests else 'TARGET_SERVICE_NOT_OBSERVED'
        elif fault == 'DOWNSTREAM_TIMEOUT':
            count = edges.get(str(target), 0)
            status = 'EDGE_OBSERVED_FAULT_EFFECT_UNPROVEN' if count >= min_unique_requests else 'TARGET_EDGE_NOT_OBSERVED'
        else:
            status, count = 'UNKNOWN_MECHANISM_BLOCKED', 0
        counts[status] += 1
        per_fault[str(fault)][status] += 1
        if (status in ('TARGET_EDGE_NOT_OBSERVED', 'TARGET_SERVICE_NOT_OBSERVED', 'UNKNOWN_MECHANISM_BLOCKED')
                or (fault is None and count < min_unique_requests)
                or (fault in ('DATABASE_LOCK_CONTENTION', 'CONNECTION_POOL_EXHAUSTION', 'DATABASE_HIGH_LATENCY', 'HIGH_LOAD', 'AUTHENTICATION_FAILURE_BURST') and count < min_unique_requests)):
            issues.append({'run_id': row['run_id'], 'fault_id': fault, 'target': target,
                           'split': split, 'reason': status, 'reference_request_count': count})
    return {'assessed_runs': len(rows), 'status_counts': dict(sorted(counts.items())),
            'issues': issues,
            'issues_by_fault': {k: dict(sorted(v.items())) for k, v in sorted(per_fault.items())},
            'rate_envelope_approved': False, 'fault_exposure_approved': False,
            'all_runs_remain_blocked': True}


def audit(root: Path, draft: Path, pilot: Path) -> dict:
    plan = read_draft(draft)
    run_id, requests, events = load_verified_pilot(pilot)
    coverage = extract_coverage(events)
    if coverage['service_request_counts'].get('gateway', 0) < 10:
        raise ValueError('healthy pilot has insufficient gateway observations')
    findings = assess_matrix(plan['runs'], coverage)
    return {'schema_version': '19c2f.path-audit.1',
            'draft_sha256': plan['draft_sha256'], 'healthy_reference_run_id': run_id,
            'reference_request_count': len(requests), 'reference_event_count': len(events),
            'coverage': coverage, 'findings': findings,
            'scientific_limit': 'Healthy calls prove path reachability only; they do not prove fault impact or safe RPS'}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--audit', action='store_true')
    p.add_argument('--project-root', type=Path, default=ROOT)
    p.add_argument('--draft', type=Path, default=DEFAULT_DRAFT)
    p.add_argument('--healthy-pilot', type=Path, default=DEFAULT_PILOT)
    args = p.parse_args(argv)
    if args.dry_run:
        print('DRY RUN: read-only path coverage audit; no Docker, experiments or training')
        print('DRAFT:', args.draft, 'HEALTHY PILOT:', args.healthy_pilot)
        print('All 240 matrix rows remain blocked; no RPS approvals created.')
        return 0
    root = args.project_root.resolve()
    draft = args.draft if args.draft.is_absolute() else root / args.draft
    pilot = args.healthy_pilot if args.healthy_pilot.is_absolute() else root / args.healthy_pilot
    try:
        result = audit(root, draft, pilot)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print('STOP: invalid/unsupported evidence:', exc, file=sys.stderr)
        return 3
    cov, findings = result['coverage'], result['findings']
    print('PHASE 19C.2F — READ-ONLY RESEARCH DEPENDENCY COVERAGE')
    print('DRAFT VERIFIED:', result['draft_sha256'], '240 blocked runs')
    print('HEALTHY REFERENCE:', result['healthy_reference_run_id'],
          'requests=', result['reference_request_count'], 'events=', result['reference_event_count'])
    print('OBSERVED EDGES (distinct correlated requests):')
    for edge, n in cov['edge_request_counts'].items():
        print(f'  {edge:32s} {n}')
    print('OBSERVED SERVICES (distinct correlated requests):', cov['service_request_counts'])
    print('SCENARIO ASSESSMENT COUNTS:', findings['status_counts'])
    print('UNSUPPORTED/WEAK TARGET ROWS:', len(findings['issues']))
    for row in findings['issues'][:30]:
        print('  NO-GO:', row['run_id'], row['fault_id'], row['target'],
              row['reason'], f"seen={row['reference_request_count']}")
    if len(findings['issues']) > 30:
        print(f"  ... additional {len(findings['issues'])-30} rows omitted")
    print('RATE ENVELOPE: NOT APPROVED; FAULT IMPACT: NOT PROVEN; ALL 240 RUNS REMAIN BLOCKED')
    print('OBSERVATIONAL LIMIT: an edge absent from one healthy run may require further verification; never assume fault observability.')
    if findings['issues']:
        print('VERDICT: NO_GO_UNOBSERVABLE_OR_INSUFFICIENT_TARGETS — correct audit exit code 2')
        return 2
    print('VERDICT: REACHABILITY_ONLY_PROVISIONAL — no experiments authorized')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
