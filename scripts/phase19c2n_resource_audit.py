#!/usr/bin/env python3
"""Independent offline six-artifact resource-fault diagnostic auditor."""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2d_integrity_audit import (EVENT_KEYS, FORBIDDEN, REQUEST_KEYS,
                                                 load_json, load_jsonl)
from scripts.phase19c2d_fault_pilot import correlation_gate, recovery_gate, parsed_time
from scripts.phase19c2n_resource_pilot import (SCHEMA, INTENSITY, RATE, TARGET, SOURCE_SCRIPT,
                                                configuration, effect_gate)

ARTIFACTS = frozenset({'requests.jsonl', 'recovery_requests.jsonl', 'events.jsonl',
                       'database_samples.jsonl', 'candidate_feature.json',
                       'protected_ground_truth.json'})
RUN_RE = re.compile(r'v2resource-[0-9a-f]{16}\Z')


def audit(root: Path, directory: Path) -> int:
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features

    root = root.resolve(strict=True)
    base = root / 'data/datasets/v2/pilot-fault'
    if base.is_symlink():
        raise ValueError('Symlink pilot root not allowed')
    folder = directory.resolve(strict=True)
    if not folder.is_dir() or folder.parent != base.resolve(strict=True) or not RUN_RE.fullmatch(folder.name):
        raise ValueError('Untrusted resource diagnostic directory')
    run_id = folder.name
    manifest = load_json(folder / 'manifest.json')
    candidate = load_json(folder / 'candidate_feature.json')
    truth = load_json(folder / 'protected_ground_truth.json')
    if any(item.get('run_id') != run_id or item.get('schema_version') != SCHEMA
           for item in (manifest, candidate, truth)):
        raise ValueError('Incompatible run / schema provenance')
    if (manifest.get('split') != 'diagnostic_only' or candidate.get('split') != 'diagnostic_only' or
        manifest.get('training_eligible') is not False or candidate.get('training_eligible') is not False or
        manifest.get('threshold_tuning_eligible') is not False or candidate.get('threshold_tuning_eligible') is not False or
        candidate.get('label') is not None or candidate.get('candidate_status') != 'UNLABELED_RESOURCE_DIAGNOSTIC' or
        candidate.get('feature_count') != 65 or candidate.get('window_seconds') != 60):
        raise ValueError('Resource diagnostic no-training contract violated')
    if manifest.get('feature_schema_sha256') != hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest():
        raise ValueError('Feature schema hash mismatch')
    fault = truth.get('fault_id')
    configuration(fault, truth.get('target'), RATE)
    if truth.get('intensity') != INTENSITY[fault] or truth.get('truth_role') != 'protected validation only; excluded from model features':
        raise ValueError('Protected truth incompatible')
    items = manifest.get('artifacts')
    if not isinstance(items, dict) or set(items) != ARTIFACTS:
        raise ValueError('Artifact inventory mismatch')
    for name in ARTIFACTS:
        path = folder / name
        if path.is_symlink():
            raise ValueError('Symlink artifact forbidden')
        raw = path.read_bytes()
        expected = items[name]
        if expected['bytes'] != len(raw) or expected['sha256'] != hashlib.sha256(raw).hexdigest():
            raise ValueError(f'Artifact checksum mismatch: {name}')
    requests, recovery, events = (load_jsonl(folder / name) for name in
                                  ('requests.jsonl', 'recovery_requests.jsonl', 'events.jsonl'))
    samples = load_jsonl(folder / 'database_samples.jsonl')
    if not requests or not events or len(recovery) != 8 or not samples:
        raise ValueError('Missing client, event, recovery, or physical samples')
    for r in requests + recovery:
        if (set(r) - REQUEST_KEYS or set(r) & FORBIDDEN or r.get('run_id') != run_id or
            r.get('scenario') != 'valid'):
            raise ValueError('Client observation schema/provenance violation')
    for e in events:
        if set(e) - EVENT_KEYS or set(e) & FORBIDDEN or e.get('run_id') != run_id:
            raise ValueError('Service event schema/provenance violation')
    for sample in samples:
        if (set(sample) != {'timestamp','total_connections','advisory_waiters','advisory_holders'} or
            any(isinstance(sample[k], bool) or not isinstance(sample[k], int) or sample[k] < 0
                for k in ('total_connections','advisory_waiters','advisory_holders'))):
            raise ValueError('Malformed physical DB observations')
        parsed_time(sample['timestamp'])
    ids = [r.get('request_id') for r in requests + recovery]
    if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i.startswith(run_id + '-request-') for i in ids):
        raise ValueError('Client IDs not unique / not run scoped')
    correlation_gate(requests + recovery, events)
    meta = manifest.get('metadata')
    if (not isinstance(meta, dict) or meta.get('source_script') != SOURCE_SCRIPT or
        meta.get('scheduled_rps') != RATE or meta.get('training_eligibility') is not False or
        meta.get('passed_pre_post_stack_health') is not True or
        meta.get('observed_requests') != len(requests) or meta.get('observed_events') != len(events)):
        raise ValueError('Manifest metadata inconsistent')
    preflight = meta.get('preflight')
    if (not isinstance(preflight, dict) or preflight.get('fault') != fault or
        preflight.get('remaining_after_allowance', -1) < 40):
        raise ValueError('Missing approved connection headroom proof')
    window = select_exact_window(phase_started_at=meta['phase_started_at'],
                                 phase_ended_at=meta['phase_ended_at'],
                                 telemetry_watermark=meta['telemetry_watermark'],
                                 fault_activated_at=truth['activated_at'],
                                 fault_expires_at=truth['expires_at'],
                                 fault_cleared_at=truth['cleared_at'],
                                 fault_required=True, lead_guard_seconds=2,
                                 tail_guard_seconds=2)
    if (window.started_at != parsed_time(manifest['window_started_at']) or
        window.ended_at != parsed_time(manifest['window_ended_at']) or
        window.started_at != parsed_time(candidate['window_started_at']) or
        window.ended_at != parsed_time(candidate['window_ended_at'])):
        raise ValueError('Exact-window boundary mismatch')
    reproduced = build_observable_features(run_id=run_id, window=window,
                                           requests=requests, events=events,
                                           require_boundary_evidence=True)
    if tuple(reproduced) != FEATURE_NAMES or reproduced != candidate.get('features'):
        raise ValueError('65 observable features do not reproduce')
    effect = effect_gate(window, requests, events, samples, fault, preflight)
    recovered = recovery_gate(recovery, events, cleared_at=parsed_time(truth['cleared_at']))
    if effect != meta.get('impact') or recovered != meta.get('recovery'):
        raise ValueError('Impact or post-lease recovery do not reproduce')
    print('RESOURCE PILOT CHECKSUMS: 6/6 verified')
    print('RUN ID:', run_id, 'FAULT:', fault, 'TARGET:', TARGET)
    print('PHYSICAL DATABASE EVIDENCE:', effect)
    print('RECOVERY:', recovered)
    print('FEATURE PARITY: 65/65; protected truth excluded')
    print('VERDICT: VALID_RESOURCE_DIAGNOSTIC — NOT TRAINING ELIGIBLE')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project-root', type=Path, default=Path.cwd())
    p.add_argument('--pilot-dir', type=Path, required=True)
    a = p.parse_args(argv)
    return audit(a.project_root, a.pilot_dir)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError) as exc:
        print('RESOURCE AUDIT STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
