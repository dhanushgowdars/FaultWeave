#!/usr/bin/env python3
"""Independent, read-only recomputation and create-only admission receipt for NEW v2 runs.

Never trusts a declared training_eligible flag as proof. Raw artifacts, exact
window, outgoing run IDs, client/service correlation, fault impact and recovery
must all agree. No diagnostic or historical promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from scripts.faultweave_v2_capture import ROOT, COUNTS, FAULT, run_dir, split_for, serial, write_new


def read_json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding='utf-8'),
                     parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(obj, dict):
        raise ValueError('Expected JSON object')
    return obj


def jsonl(path: Path) -> list[dict]:
    items = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                items.append(json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))))
    if not items or not all(isinstance(item, dict) for item in items):
        raise ValueError('Empty or nonobject raw observations')
    return items


def check_sha(path: Path, sha: str, size: int) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise ValueError(f'Invalid or missing observation: {path.name}')
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError(f'Immutable evidence digest mismatch: {path.name}')


def audit(label: str, slot: int, *, write: bool = False) -> dict:
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from scripts.phase19c2c_healthy_pilot import validate_correlations
    from scripts.phase19c2d_fault_pilot import correlation_gate, recovery_gate, fault_evidence_gate, parsed_time

    split = split_for(label, slot)
    directory = run_dir(label, slot)
    if (not directory.is_dir() or directory.is_symlink() or directory.resolve().parent !=
        (ROOT / 'data/datasets/v2/training-captures').resolve()):
        raise ValueError('Invalid v2 capture directory')
    intent_path = directory / 'intent.json'
    manifest_path = directory / 'source_manifest.json'
    if (intent_path.is_symlink() or manifest_path.is_symlink()):
        raise ValueError('Unexpected evidence symlink')
    intent, manifest = read_json(intent_path), read_json(manifest_path)
    run_id = intent.get('run_id')
    if (not isinstance(run_id, str) or len(run_id) != 24 or not run_id.startswith('v2train-') or
        intent.get('schema_version') != 'faultweave-v2-training-intent.1' or
        intent.get('purpose') != 'new independent training capture, NOT historical/diagnostic promotion' or
        intent.get('slot') != slot or intent.get('label') != label or intent.get('split') != split or
        intent.get('rps') != 1.5 or intent.get('duration_seconds') != 75 or
        intent.get('scenario_type') != ('normal' if label == 'NORMAL' else 'known_fault') or
        intent.get('target') != (None if label == 'NORMAL' else 'payment') or
        intent.get('intensity') != (None if label == 'NORMAL' else 'medium')):
        raise ValueError('Precommitted new-run intent mismatch')
    if (manifest.get('schema_version') != 'faultweave-v2-training-capture.1' or
        manifest.get('run_id') != run_id or manifest.get('split') != split or
        manifest.get('label') != label or manifest.get('scenario_type') != intent['scenario_type'] or
        manifest.get('training_eligible') is not True or
        manifest.get('threshold_tuning_eligible') is not (split == 'validation') or
        manifest.get('window_seconds') != 60 or
        manifest.get('admission_requires_separate_independent_audit') is not True or
        manifest.get('outgoing_header') != 'X-Run-ID' or
        manifest.get('client_provenance') != 'experiments.runner.execute_plan' or
        manifest.get('intent_sha256') != hashlib.sha256(intent_path.read_bytes()).hexdigest() or
        manifest.get('feature_schema_sha256') != hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest()):
        raise ValueError('Source training manifest mismatch')
    expected_files = {'requests.jsonl', 'events.jsonl', 'recovery_requests.jsonl',
                      'feature_row.json', 'capture_metadata.json'}
    if label != 'NORMAL':
        expected_files.add('protected_ground_truth.json')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, dict) or set(artifacts) != expected_files:
        raise ValueError('Missing or unexpected raw evidence artifact')
    for name, receipt in artifacts.items():
        if not isinstance(receipt, dict):
            raise ValueError('Malformed raw checksum entry')
        check_sha(directory / name, receipt['sha256'], receipt['bytes'])
    requests = jsonl(directory / 'requests.jsonl')
    events = jsonl(directory / 'events.jsonl')
    recovery_bytes = (directory / 'recovery_requests.jsonl').read_text(encoding='utf-8')
    recovery = jsonl(directory / 'recovery_requests.jsonl') if recovery_bytes.strip() else []
    metadata = read_json(directory / 'capture_metadata.json')
    row = read_json(directory / 'feature_row.json')
    if (row.get('run_id') != run_id or row.get('split') != split or row.get('label') != label or
        row.get('scenario_type') != intent['scenario_type'] or row.get('training_eligible') is not True or
        row.get('threshold_tuning_eligible') is not (split == 'validation') or
        row.get('interval') != ('normal' if label == 'NORMAL' else 'fault') or
        row.get('window_seconds') != 60 or not isinstance(row.get('features'), dict) or set(row['features']) != set(FEATURE_NAMES) or
        len(row['features']) != 65 or
        any(type(v) not in (int, float) or not math.isfinite(v) for v in row['features'].values())):
        raise ValueError('Feature row label/schema mismatch')
    if (metadata.get('healthy_pre_post') is not True or
        metadata.get('scheduled_rps') != 1.5 or
        metadata.get('scheduled_requests') != len(requests) or
        len(requests) != round(75 * 1.5)):
        raise ValueError('Missing baseline/exposure evidence')
    client_ids = [r.get('request_id') for r in requests + recovery]
    if (len(client_ids) != len(set(client_ids)) or
        any(not isinstance(rid, str) or not rid.startswith(run_id + '-request-') for rid in client_ids) or
        any(r.get('run_id') != run_id or r.get('scenario') != 'valid' or not r.get('trace_id')
            or not r.get('started_at') for r in requests + recovery) or
        any(e.get('run_id') != run_id for e in events)):
        raise ValueError('New-run client/structured event provenance invalid')
    correlation_gate(requests + recovery, events)
    watermark = max(parsed_time(e['timestamp']) for e in events)
    if parsed_time(metadata['telemetry_watermark']) != watermark:
        raise ValueError('Event watermark mismatch')
    begin, end = parsed_time(metadata['phase_started_at']), parsed_time(metadata['phase_ended_at'])
    if not parsed_time(intent['collection_started_at']) <= begin < end:
        raise ValueError('Capture intent was not recorded before observation phase')
    if label == 'NORMAL':
        if recovery or 'protected_ground_truth.json' in artifacts:
            raise ValueError('Healthy run carries fault/recovery evidence')
        validate_correlations(requests, events)
        window = select_exact_window(phase_started_at=begin, phase_ended_at=end,
                                     telemetry_watermark=watermark, fault_required=False,
                                     lead_guard_seconds=2, tail_guard_seconds=2)
    else:
        truth = read_json(directory / 'protected_ground_truth.json')
        if (truth.get('run_id') != run_id or truth.get('fault_id') != FAULT or
            truth.get('target') != 'payment' or truth.get('intensity') != 'medium'):
            raise ValueError('Incorrect training known-fault evidence')
        for key in ('activated_at', 'expires_at', 'cleared_at'):
            if parsed_time(metadata['fault_' + key]) != parsed_time(truth[key]):
                raise ValueError('Fault activation/cleanup disagreement')
        window = select_exact_window(phase_started_at=begin, phase_ended_at=end,
                                     telemetry_watermark=watermark,
                                     fault_activated_at=truth['activated_at'],
                                     fault_expires_at=truth['expires_at'],
                                     fault_cleared_at=truth['cleared_at'], fault_required=True,
                                     lead_guard_seconds=2, tail_guard_seconds=2)
        proof = recovery_gate(recovery, events, cleared_at=parsed_time(truth['cleared_at']))
        if proof != metadata.get('recovery'):
            raise ValueError('Recovery independent recheck mismatch')
        impact = fault_evidence_gate(window, requests, events)
        if impact != metadata.get('impact'):
            raise ValueError('Fault effect independently disagreed')
    if (manifest['window_started_at'] != window.started_at.isoformat() or
        manifest['window_ended_at'] != window.ended_at.isoformat() or
        row['window_started_at'] != window.started_at.isoformat() or
        row['window_ended_at'] != window.ended_at.isoformat()):
        raise ValueError('Exact 60s window boundaries disagreed')
    observed_vector = build_observable_features(run_id=run_id, window=window,
                                               requests=requests, events=events,
                                               require_boundary_evidence=True)
    if observed_vector != row['features']:
        raise ValueError('65-feature recomputation mismatch')
    result = {'schema_version': 'faultweave-v2-independent-audit.1',
              'run_id': run_id, 'label': label, 'split': split,
              'verdict': 'VALID_V2_TRAINING_RUN', 'independent': True,
              'request_count': len(requests), 'correlated_event_count': len(events),
              'recovery_count': len(recovery), 'window_seconds': 60, 'feature_count': 65,
              'source_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    if write:
        write_new(directory / 'independent_audit.json', serial(result))
        # FastTrack expects these fields when combined into the final 126-run receipt book.
        receipt = {'run_id': run_id, 'label': label, 'split': split,
                   'scenario_type': intent['scenario_type'], 'training_admission': True,
                   'audit_verdict': result['verdict'],
                   'source_manifest': {'path': str(manifest_path.relative_to(ROOT)).replace('\\','/'),
                                       'sha256': result['source_manifest_sha256']},
                   'independent_audit': {'path': str((directory/'independent_audit.json').relative_to(ROOT)).replace('\\','/'),
                                          'sha256': hashlib.sha256(serial(result)).hexdigest()}}
        write_new(directory / 'admission_receipt.json', serial(receipt))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--admit', action='store_true')
    parser.add_argument('--label', choices=sorted(COUNTS), required=True)
    parser.add_argument('--slot', type=int, required=True)
    args = parser.parse_args(argv)
    result = audit(args.label, args.slot, write=args.admit)
    print(json.dumps(result, indent=2))
    if not args.admit:
        print('READ ONLY: no training admission files were created')
    else:
        print('ADMITTED ONE NEW CAPTURE: not a deployed model; no changes to original 240-run proposal')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        print('V2 INDEPENDENT AUDIT STOP:', exc)
        raise SystemExit(2)
