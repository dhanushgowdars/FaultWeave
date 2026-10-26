#!/usr/bin/env python3
"""19C.2G: derive a BLOCKED reachability-corrected proposal from a verified healthy pilot.

This file does not execute any run, mutate its parent draft, access Docker or train.
It changes only 10 DOWNSTREAM_TIMEOUT targets, preserving all other run fields.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2e_matrix import validate_draft  # noqa: E402
from scripts.phase19c2f_path_audit import (  # noqa: E402
    assess_matrix, canonical_digest, extract_coverage, load_verified_pilot, read_draft,
)

PARENT = Path('data/datasets/v2/plan/phase19c2e-draft-matrix.json')
PILOT = Path('data/datasets/v2/pilot/v2pilot-01f0974b69ce4220')
OUTPUT = Path('data/datasets/v2/plan/phase19c2g-reachability-proposal.json')
PARENT_DIGEST = '9eed555e72e378a8c7e7d6dc3e3d3724a716fce3b96dafc72d841fd0403567b5'
EXPECTED_UNREACHABLE = {'payment->account', 'ledger->account'}
ELIGIBLE_TIMEOUT_EDGES = ('transaction->payment', 'payment->ledger')
MIN_DISTINCT_REQUESTS = 10
VERSION = '19c2g-reachability-proposal.1'


def path(root: Path, name: Path) -> Path:
    return name if name.is_absolute() else root / name


def prepare(parent: dict, coverage: dict, pilot_id: str) -> dict:
    """Pure, fail-closed derivation; never enables a run or invents successful fault evidence."""
    if parent.get('draft_sha256') != PARENT_DIGEST:
        raise ValueError('parent SHA does not match the audited 19C.2E matrix')
    validate_draft(parent)
    original = parent['runs']
    counts = coverage['edge_request_counts']
    if any(counts.get(edge, 0) < MIN_DISTINCT_REQUESTS for edge in ELIGIBLE_TIMEOUT_EDGES):
        raise ValueError('insufficient verified observations on replacement dependency edges')
    before = assess_matrix(original, coverage, min_unique_requests=MIN_DISTINCT_REQUESTS)
    bad = before['issues']
    if len(bad) != 10:
        raise ValueError(f'expected exactly 10 original unsupported targets, found {len(bad)}')
    if any(x['fault_id'] != 'DOWNSTREAM_TIMEOUT' or x['target'] not in EXPECTED_UNREACHABLE
           or x['reason'] != 'TARGET_EDGE_NOT_OBSERVED' for x in bad):
        raise ValueError('unexpected unsupported mechanism; no automatic repair permitted')
    # Derived from the REAL healthy research calls; never use fault label to choose targets at inference.
    proposal = copy.deepcopy(parent)
    proposal['schema_version'] = VERSION
    proposal['status'] = 'DRAFT_BLOCKED_NOT_APPROVED'
    proposal['purpose'] = 'non-executable post-audit reachability proposal; original 19C.2E immutable'
    proposal['source_draft_sha256'] = PARENT_DIGEST
    proposal['source_reference_run_id'] = pilot_id
    proposal['reference_edge_distinct_requests'] = {
        edge: counts[edge] for edge in ELIGIBLE_TIMEOUT_EDGES
    }
    proposal['reachability_only_not_fault_effect_proof'] = True
    proposal['approval'] = {
        'rate_envelope': False,
        'fault_exposure_per_target': False,
        'infrastructure_disruption': False,
        'training_eligibility': False,
        'fresh_unknown_holdout': False,
        'bulk_execution': False,
    }
    # Spread the observed edges across each split while retaining all run IDs and original run fields.
    # For each split, retain the previously supported placements before assigning new targets.
    split_counts = {
        split: Counter(row['target'] for row in original
                       if row['fault_id'] == 'DOWNSTREAM_TIMEOUT'
                       and row['split'] == split
                       and row['target'] in ELIGIBLE_TIMEOUT_EDGES)
        for split in ('train', 'validation', 'test')
    }
    changes = []
    unsupported_ids = {x['run_id'] for x in bad}
    for row in proposal['runs']:
        if row['run_id'] not in unsupported_ids:
            continue
        split = row['split']
        previous = row['target']
        chosen = min(ELIGIBLE_TIMEOUT_EDGES, key=lambda edge: (split_counts[split][edge], ELIGIBLE_TIMEOUT_EDGES.index(edge)))
        split_counts[split][chosen] += 1
        row['target'] = chosen
        changes.append({'run_id': row['run_id'], 'split': split,
                        'fault_id': row['fault_id'], 'from': previous, 'to': chosen,
                        'reason': 'source edge absent in verified healthy research trace'})
    if len(changes) != 10:
        raise ValueError('unexpected number of modifications')
    proposal['target_changes'] = changes
    proposal['draft_sha256'] = canonical_digest(proposal)
    verify_proposal(parent, proposal, coverage, pilot_id)
    return proposal


def verify_proposal(parent: dict, proposal: dict, coverage: dict, pilot_id: str) -> None:
    if proposal.get('schema_version') != VERSION or proposal.get('source_draft_sha256') != PARENT_DIGEST:
        raise ValueError('wrong proposal version or parent provenance')
    if proposal.get('source_reference_run_id') != pilot_id:
        raise ValueError('wrong healthy reference')
    if proposal.get('draft_sha256') != canonical_digest(proposal):
        raise ValueError('candidate proposal checksum mismatch')
    if not proposal.get('reachability_only_not_fault_effect_proof'):
        raise ValueError('fault-effect evidence must NOT be implied')
    if any(value is not False for value in proposal.get('approval', {}).values()) or len(proposal.get('approval', {})) != 6:
        raise ValueError('unexpected approval field')
    validate_draft(proposal)
    # Never let a corrected path proposal silently change rate calibration,
    # split policy, acceptance requirements, feature schema, or any other plan metadata.
    permitted_plan_fields = {
        'schema_version', 'purpose', 'runs', 'draft_sha256',
        'source_draft_sha256', 'source_reference_run_id',
        'reference_edge_distinct_requests', 'reachability_only_not_fault_effect_proof',
        'approval', 'target_changes',
    }
    unexpected_fields = set(proposal) - set(parent) - permitted_plan_fields
    if unexpected_fields:
        raise ValueError(f'unexpected proposal metadata: {sorted(unexpected_fields)}')
    for field in parent:
        if field not in permitted_plan_fields and parent[field] != proposal.get(field):
            raise ValueError(f'unexplained parent metadata change: {field}')
    if proposal['reference_edge_distinct_requests'] != {
        edge: coverage['edge_request_counts'][edge] for edge in ELIGIBLE_TIMEOUT_EDGES
    }:
        raise ValueError('healthy edge provenance mismatch')
    if len(parent['runs']) != len(proposal['runs']):
        raise ValueError('lost run rows')
    changes = proposal.get('target_changes', [])
    if len(changes) != 10 or len({c['run_id'] for c in changes}) != 10:
        raise ValueError('incomplete/duplicate target provenance')
    changes_by_id = {c['run_id']: c for c in changes}
    for before, after in zip(parent['runs'], proposal['runs'], strict=True):
        if before['run_id'] != after['run_id']:
            raise ValueError('run order/identity altered')
        differences = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
        if before['run_id'] not in changes_by_id:
            if differences:
                raise ValueError(f'unexplained changes to {before["run_id"]}')
        else:
            log = changes_by_id[before['run_id']]
            if differences != {'target'} or log['from'] != before['target'] or log['to'] != after['target']:
                raise ValueError('change log does not match a target-only modification')
            if log['split'] != before['split'] or log['fault_id'] != 'DOWNSTREAM_TIMEOUT':
                raise ValueError('change provenance does not match run')
            if before['target'] not in EXPECTED_UNREACHABLE or after['target'] not in ELIGIBLE_TIMEOUT_EDGES:
                raise ValueError('unsupported target reassignment')
    after_status = assess_matrix(proposal['runs'], coverage, min_unique_requests=MIN_DISTINCT_REQUESTS)
    if after_status['issues']:
        raise ValueError('proposed matrix still contains unsupported targets')
    timeout_rows = [r for r in proposal['runs'] if r['fault_id'] == 'DOWNSTREAM_TIMEOUT']
    for split, expected in (('train', 6), ('validation', 2), ('test', 2)):
        split_distribution = Counter(r['target'] for r in timeout_rows if r['split'] == split)
        if split_distribution != {edge: expected for edge in ELIGIBLE_TIMEOUT_EDGES}:
            raise ValueError(f'timeout targets unbalanced in split {split}: {split_distribution}')


def build(root: Path, draft: Path, healthy_pilot: Path) -> tuple[dict, dict, dict]:
    parent = read_draft(draft)
    pilot_id, _, events = load_verified_pilot(healthy_pilot)
    coverage = extract_coverage(events)
    derived = prepare(parent, coverage, pilot_id)
    return parent, derived, coverage


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--write', action='store_true')
    mode.add_argument('--verify', action='store_true')
    ap.add_argument('--project-root', type=Path, default=ROOT)
    ap.add_argument('--draft', type=Path, default=PARENT)
    ap.add_argument('--healthy-pilot', type=Path, default=PILOT)
    ap.add_argument('--output', type=Path, default=OUTPUT)
    args = ap.parse_args(argv)
    root = args.project_root.resolve()
    source = path(root, args.draft)
    pilot = path(root, args.healthy_pilot)
    output = path(root, args.output)
    try:
        parent, derived, coverage = build(root, source, pilot)
        changes = derived['target_changes']
        splits = Counter(row['split'] for row in derived['runs'])
        print('PHASE 19C.2G — BLOCKED REACHABILITY CORRECTION PROPOSAL')
        print('PARENT SHA256:', parent['draft_sha256'])
        print('HEALTHY PILOT:', derived['source_reference_run_id'])
        print('EXACT CHANGES:', len(changes), 'timeout targets only')
        for row in changes:
            print('  CHANGE:', row['run_id'], row['from'], '=>', row['to'])
        print('PROPOSAL:', len(derived['runs']), 'runs; split counts', dict(splits))
        print('REVISED SHA256:', derived['draft_sha256'])
        print('RATE/FAULT APPROVAL: NO; ALL RUNS REMAIN BLOCKED')
        if args.dry_run:
            print('DRY RUN: no files written')
            return 0
        if args.verify:
            saved = json.loads(output.read_text(encoding='utf-8'))
            if saved != derived:
                raise ValueError('saved plan does not match deterministic verified proposal')
            print('VERIFIED: saved blocked proposal identical; source unchanged by this tool')
            return 0
        # Write is create-only and refuses ANY pre-existing output, including symlinks.
        if output.exists() or output.is_symlink():
            raise FileExistsError(f'no overwrite allowed: {output}')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(derived, handle, indent=2, sort_keys=True)
            handle.write('\n')
        print('CREATED BLOCKED PROPOSAL:', output)
        print('NO original draft, fault, Docker, model, database, network, or Git changes')
        return 0
    except (ValueError, KeyError, OSError, TypeError) as err:
        print('STOP:', err, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
