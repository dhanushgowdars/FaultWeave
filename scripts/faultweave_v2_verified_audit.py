#!/usr/bin/env python3
"""Independent read-only evidence reconstruction for five NEW v2 fault collectors.

Labels and fault leases come from precommitted intent and protected truth only.
Raw immutable client/events and physical DB samples determine admissibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from scripts import faultweave_v2_capture as base
from scripts import faultweave_v2_independent_audit as original
from scripts.faultweave_v2_verified_capture import SPECS, spec, split_for, slot_path


def audit(label: str, slot: int, *, write: bool = False) -> dict:
    target, intensity, mechanism = spec(label)
    split = split_for(label, slot)
    directory = slot_path(label, slot)
    if (not directory.is_dir() or directory.is_symlink() or directory.resolve().parent !=
            base.RUNS.resolve()):
        raise ValueError('STOP: invalid capture directory')
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from scripts.phase19c2d_fault_pilot import parsed_time, correlation_gate, recovery_gate
    from scripts.phase19c2j_timeout_pilot import timeout_impact
    from scripts.phase19c2m_service_effect_pilot import observed_impact
    from scripts.phase19c2n_resource_pilot import effect_gate
    intent_path, manifest_path = directory/'intent.json', directory/'source_manifest.json'
    if intent_path.is_symlink() or manifest_path.is_symlink():
        raise ValueError('STOP: evidence symlink')
    intent, manifest = original.read_json(intent_path), original.read_json(manifest_path)
    run_id = intent.get('run_id')
    if (not isinstance(run_id, str) or not run_id.startswith('v2train-') or len(run_id) != 24 or
        intent.get('schema_version') != 'faultweave-v2-training-intent.1' or
        intent.get('purpose') != 'new independent training capture, NOT historical/diagnostic promotion' or
        intent.get('collector') != 'verified-five.1' or intent.get('mechanism') != mechanism or
        intent.get('slot') != slot or intent.get('split') != split or
        intent.get('label') != label or intent.get('scenario_type') != 'known_fault' or
        intent.get('target') != target or intent.get('intensity') != intensity or
        intent.get('rps') != base.RPS or intent.get('duration_seconds') != base.DURATION):
        raise ValueError('STOP: precommitted independent training intent mismatch')
    if (manifest.get('schema_version') != 'faultweave-v2-training-capture.1' or
        manifest.get('run_id') != run_id or manifest.get('label') != label or
        manifest.get('split') != split or manifest.get('scenario_type') != 'known_fault' or
        manifest.get('training_eligible') is not True or
        manifest.get('threshold_tuning_eligible') is not (split == 'validation') or
        manifest.get('window_seconds') != 60 or
        manifest.get('admission_requires_separate_independent_audit') is not True or
        manifest.get('outgoing_header') != 'X-Run-ID' or
        manifest.get('client_provenance') != 'experiments.runner.execute_plan' or
        manifest.get('intent_sha256') != hashlib.sha256(intent_path.read_bytes()).hexdigest() or
        manifest.get('feature_schema_sha256') != hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest()):
        raise ValueError('STOP: source training manifest mismatch')
    expected = {'requests.jsonl', 'recovery_requests.jsonl', 'events.jsonl',
                'feature_row.json', 'capture_metadata.json', 'protected_ground_truth.json'}
    if mechanism == 'resource':
        expected.add('db_samples.jsonl')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, dict) or set(artifacts) != expected:
        raise ValueError('STOP: incomplete source artifact declaration')
    for name, rec in artifacts.items():
        if (not isinstance(rec, dict) or type(rec.get('bytes')) is not int or
                not isinstance(rec.get('sha256'), str)):
            raise ValueError('STOP: invalid source SHA256 contract')
        original.check_sha(directory/name, rec['sha256'], rec['bytes'])
    requests = original.jsonl(directory/'requests.jsonl')
    recovery = original.jsonl(directory/'recovery_requests.jsonl')
    events = original.jsonl(directory/'events.jsonl')
    samples = original.jsonl(directory/'db_samples.jsonl') if mechanism == 'resource' else None
    row, metadata, truth = (original.read_json(directory/name) for name in
                           ('feature_row.json', 'capture_metadata.json', 'protected_ground_truth.json'))
    if (row.get('schema_version') != 'faultweave-v2-feature-row.1' or
        row.get('run_id') != run_id or row.get('split') != split or row.get('label') != label or
        row.get('scenario_type') != 'known_fault' or row.get('interval') != 'fault' or
        row.get('window_seconds') != 60 or row.get('training_eligible') is not True or
        row.get('threshold_tuning_eligible') is not (split == 'validation') or
        not isinstance(row.get('features'), dict) or set(row['features']) != set(FEATURE_NAMES) or
        len(row['features']) != 65 or
        any(type(value) not in (float, int) or not math.isfinite(value)
            for value in row['features'].values())):
        raise ValueError('STOP: 65 observable feature schema or label mismatch')
    if (truth.get('run_id') != run_id or truth.get('fault_id') != label or
        truth.get('target') != target or truth.get('intensity') != intensity or
        truth.get('truth_role') != 'audit_only_not_model_feature' or
        metadata.get('healthy_pre_post') is not True or
        metadata.get('mechanism') != mechanism or
        metadata.get('scheduled_rps') != base.RPS or
        metadata.get('scheduled_requests') != len(requests) or
        len(requests) != round(base.DURATION * base.RPS)):
        raise ValueError('STOP: activation/traffic metadata inconsistent')
    ids = [r.get('request_id') for r in requests + recovery]
    if (len(ids) != len(set(ids)) or
        any(not isinstance(rid, str) or not rid.startswith(run_id+'-request-') for rid in ids) or
        any(r.get('run_id') != run_id or r.get('scenario') != 'valid' or not r.get('trace_id')
            or not r.get('started_at') for r in requests+recovery) or
        any(e.get('run_id') != run_id for e in events)):
        raise ValueError('STOP: client/event provenance mismatch')
    correlation_gate(requests+recovery, events)
    watermark = max(parsed_time(e['timestamp']) for e in events)
    if parsed_time(metadata['telemetry_watermark']) != watermark:
        raise ValueError('STOP: event telemetry watermark mismatch')
    begin, end = parsed_time(metadata['phase_started_at']), parsed_time(metadata['phase_ended_at'])
    if not parsed_time(intent['collection_started_at']) <= begin < end:
        raise ValueError('STOP: sample started before immutable intent')
    for key in ('activated_at', 'expires_at', 'cleared_at'):
        if parsed_time(metadata['fault_' + key]) != parsed_time(truth[key]):
            raise ValueError('STOP: independent lease timing mismatch')
    window = select_exact_window(phase_started_at=begin, phase_ended_at=end,
                                 telemetry_watermark=watermark,
                                 fault_activated_at=truth['activated_at'],
                                 fault_expires_at=truth['expires_at'],
                                 fault_cleared_at=truth['cleared_at'],
                                 fault_required=True, lead_guard_seconds=2,
                                 tail_guard_seconds=2)
    if (manifest.get('window_started_at') != window.started_at.isoformat() or
        manifest.get('window_ended_at') != window.ended_at.isoformat() or
        row.get('window_started_at') != window.started_at.isoformat() or
        row.get('window_ended_at') != window.ended_at.isoformat()):
        raise ValueError('STOP: exact 60s window mismatch')
    proof = recovery_gate(recovery, events, cleared_at=parsed_time(truth['cleared_at']))
    if proof != metadata.get('recovery'):
        raise ValueError('STOP: independent recovery mismatch')
    if mechanism == 'timeout':
        effect = timeout_impact(window, requests, events, target)
    elif mechanism == 'service':
        effect = observed_impact(window, requests, events, label, target)
    else:
        baseline = metadata.get('preflight')
        if (not isinstance(baseline, dict) or baseline.get('fault') != label or
                type(baseline.get('remaining_after_allowance')) is not int or
                baseline.get('remaining_after_allowance') < 40):
            raise ValueError('STOP: resource preflight absent or unsafe')
        effect = effect_gate(window, requests, events, samples, label, baseline)
    if effect != metadata.get('impact'):
        raise ValueError('STOP: independent fault impact mismatch')
    recomputed = build_observable_features(run_id=run_id, window=window,
                                           requests=requests, events=events,
                                           require_boundary_evidence=True)
    if recomputed != row['features']:
        raise ValueError('STOP: 65-feature independent recomputation mismatch')
    result = {
        'schema_version': 'faultweave-v2-independent-audit.1',
        'run_id': run_id, 'label': label, 'split': split,
        'verdict': 'VALID_V2_TRAINING_RUN', 'independent': True,
        'request_count': len(requests), 'correlated_event_count': len(events),
        'recovery_count': len(recovery), 'window_seconds': 60, 'feature_count': 65,
        'source_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    if write:
        base.write_new(directory/'independent_audit.json', base.serial(result))
        receipt = {
            'run_id': run_id, 'label': label, 'split': split,
            'scenario_type': 'known_fault', 'training_admission': True,
            'audit_verdict': result['verdict'],
            'source_manifest': {
                'path': manifest_path.relative_to(base.ROOT).as_posix(),
                'sha256': result['source_manifest_sha256'],
            },
            'independent_audit': {
                'path': (directory/'independent_audit.json').relative_to(base.ROOT).as_posix(),
                'sha256': hashlib.sha256(base.serial(result)).hexdigest(),
            },
        }
        base.write_new(directory/'admission_receipt.json', base.serial(receipt))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--admit', action='store_true')
    parser.add_argument('--label', required=True)
    parser.add_argument('--slot', type=int, required=True)
    args = parser.parse_args(argv)
    result = audit(args.label, args.slot, write=args.admit)
    print(json.dumps(result, indent=2))
    print('ADMITTED ONE NEW INDEPENDENT CAPTURE' if args.admit else 'READ ONLY — no admission writes')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, KeyError, ValueError, TypeError) as exc:
        print('V2 VERIFIED INDEPENDENT AUDIT STOP:', exc)
        raise SystemExit(2)
