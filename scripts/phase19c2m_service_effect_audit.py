#!/usr/bin/env python3
"""Independent offline audit of one unlabelled DB latency or middleware 503 pilot."""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2d_integrity_audit import (  # noqa: E402
    EVENT_KEYS, FORBIDDEN, REQUEST_KEYS, load_json, load_jsonl,
)
from scripts.phase19c2d_fault_pilot import correlation_gate, recovery_gate, parsed_time  # noqa: E402
from scripts.phase19c2m_service_effect_pilot import (  # noqa: E402
    SCHEMA, INTENSITY, RATE, SOURCE_SCRIPT, allowed_configuration, observed_impact,
)

ARTIFACTS = frozenset({'requests.jsonl', 'recovery_requests.jsonl', 'events.jsonl',
                       'candidate_feature.json', 'protected_ground_truth.json'})
PATTERN = re.compile(r'v2effect-[0-9a-f]{16}\Z')


def audit(root: Path, pilot: Path) -> int:
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features

    root = root.resolve(strict=True)
    base = root / 'data/datasets/v2/pilot-fault'
    if base.is_symlink():
        raise ValueError('Symlink pilot-output root forbidden')
    pilot = pilot.resolve(strict=True)
    if pilot.parent != base.resolve(strict=True) or not pilot.is_dir() or not PATTERN.fullmatch(pilot.name):
        raise ValueError('Unexpected or untrusted diagnostic directory')
    run_id = pilot.name
    manifest = load_json(pilot/'manifest.json')
    candidate = load_json(pilot/'candidate_feature.json')
    truth = load_json(pilot/'protected_ground_truth.json')
    if any(o.get('run_id') != run_id or o.get('schema_version') != SCHEMA
           for o in (manifest, candidate, truth)):
        raise ValueError('Schema or run provenance mismatch')
    if (manifest.get('split') != 'diagnostic_only' or candidate.get('split') != 'diagnostic_only' or
        manifest.get('training_eligible') is not False or candidate.get('training_eligible') is not False or
        manifest.get('threshold_tuning_eligible') is not False or candidate.get('threshold_tuning_eligible') is not False or
        candidate.get('label') is not None or candidate.get('candidate_status') != 'UNLABELED_SERVICE_EFFECT_DIAGNOSTIC' or
        candidate.get('feature_count') != 65 or candidate.get('window_seconds') != 60 or
        manifest.get('outgoing_header') != 'X-Run-ID' or manifest.get('client_provenance') != 'experiments.runner.execute_plan'):
        raise ValueError('Diagnostic-only / no-training invariant violated')
    fault, target = truth.get('fault_id'), truth.get('target')
    allowed_configuration(fault, target, RATE)
    if truth.get('intensity') != INTENSITY or truth.get('truth_role') != 'protected validation only; excluded from model features':
        raise ValueError('Protected truth metadata invalid')
    if manifest.get('feature_schema_sha256') != hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest():
        raise ValueError('65-feature schema changed')
    inventory = manifest.get('artifacts')
    if not isinstance(inventory, dict) or set(inventory) != ARTIFACTS:
        raise ValueError('Unexpected artifact inventory')
    for name in sorted(ARTIFACTS):
        item = inventory[name]
        path = pilot/name
        if not isinstance(item, dict) or path.is_symlink():
            raise ValueError('Symlink / malformed artifact')
        raw = path.read_bytes()
        if item.get('bytes') != len(raw) or item.get('sha256') != hashlib.sha256(raw).hexdigest():
            raise ValueError(f'Checksum mismatch: {name}')
    requests = load_jsonl(pilot/'requests.jsonl')
    recovery = load_jsonl(pilot/'recovery_requests.jsonl')
    events = load_jsonl(pilot/'events.jsonl')
    if not requests or not events or len(recovery) != 8:
        raise ValueError('Missing client / event / recovery observations')
    for r in requests+recovery:
        if set(r) - REQUEST_KEYS or set(r) & FORBIDDEN or r.get('run_id') != run_id or r.get('scenario') != 'valid':
            raise ValueError('Unexpected client fields, run or scenario')
    for e in events:
        if set(e) - EVENT_KEYS or set(e) & FORBIDDEN or e.get('run_id') != run_id:
            raise ValueError('Unexpected service event fields or run')
    ids = [r.get('request_id') for r in requests+recovery]
    if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i.startswith(run_id+'-request-') for i in ids):
        raise ValueError('Missing, duplicated or unproven request identifiers')
    correlation_gate(requests+recovery, events)
    meta = manifest.get('metadata')
    if (not isinstance(meta, dict) or meta.get('source_script') != SOURCE_SCRIPT or
        meta.get('observed_requests') != len(requests) or meta.get('observed_events') != len(events) or
        meta.get('scheduled_rps') != RATE or meta.get('passed_pre_post_stack_health') is not True or
        meta.get('training_eligibility') is not False):
        raise ValueError('Inconsistent manifest collection provenance')
    window = select_exact_window(phase_started_at=meta['phase_started_at'],
                                 phase_ended_at=meta['phase_ended_at'],
                                 telemetry_watermark=meta['telemetry_watermark'],
                                 fault_activated_at=truth['activated_at'],
                                 fault_expires_at=truth['expires_at'],
                                 fault_cleared_at=truth['cleared_at'], fault_required=True,
                                 lead_guard_seconds=2.0, tail_guard_seconds=2.0)
    if (window.started_at != parsed_time(manifest['window_started_at']) or
        window.ended_at != parsed_time(manifest['window_ended_at']) or
        window.started_at != parsed_time(candidate['window_started_at']) or
        window.ended_at != parsed_time(candidate['window_ended_at'])):
        raise ValueError('Exact window does not reproduce')
    vector = build_observable_features(run_id=run_id, window=window,
                                       requests=requests, events=events,
                                       require_boundary_evidence=True)
    if tuple(vector) != FEATURE_NAMES or candidate.get('features') != vector:
        raise ValueError('Feature vector does not reproduce or protected metadata leaked')
    impact = observed_impact(window, requests, events, fault, target)
    recovery_proof = recovery_gate(recovery, events, cleared_at=parsed_time(truth['cleared_at']))
    if impact != meta.get('impact') or recovery_proof != meta.get('recovery'):
        raise ValueError('Impact or recovery not reproducible from raw observations')
    print('ARTIFACT CHECKSUMS: 5/5 verified')
    print('RUN ID:', run_id, 'FAULT:', fault, 'TARGET:', target)
    print('WINDOW:', window.started_at.isoformat(), '->', window.ended_at.isoformat())
    print('OBSERVED EFFECT:', impact)
    print('RECOVERY:', recovery_proof)
    print('FEATURE PARITY: 65/65; protected truth excluded')
    print('VERDICT: VALID_SERVICE_EFFECT_DIAGNOSTIC — NOT TRAINING ELIGIBLE')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path.cwd())
    parser.add_argument('--pilot-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    return audit(args.project_root, args.pilot_dir)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, ImportError, KeyError, TypeError) as exc:
        print('SERVICE EFFECT AUDIT STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
