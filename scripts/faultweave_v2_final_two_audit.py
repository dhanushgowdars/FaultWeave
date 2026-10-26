#!/usr/bin/env python3
"""Independent raw-evidence reconstruction and create-only admission for final two.

No Docker mutations, traffic or model writes. Physical isolated Docker stop proof
is recorded at capture time and checked here for consistency; no retrospective
filesystem record alone can prove the historical event was real.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

from scripts import faultweave_v2_capture as base
from scripts import faultweave_v2_final_two_capture as collector
from scripts import faultweave_v2_independent_audit as original
from scripts import faultweave_v2_isolated_db as isolated
from scripts import phase19c_last_two_pilot as impact_pilot


def verified_window_phase_end(meta: dict, label: str, phase_ended_at: datetime,
                              requests: list[dict]) -> datetime:
    """Independently recalculate a versioned high-load window end cap.

    Old captures have no window-selection strategy and retain their original
    audit window calculation unchanged.
    """
    from scripts.phase19c2d_fault_pilot import parsed_time
    strategy = meta.get('window_selection_strategy')
    if strategy is None:
        if 'window_phase_ended_at' in meta:
            raise ValueError('STOP: window end field without strategy')
        return phase_ended_at
    if label != 'HIGH_LOAD' or strategy != 'high-load-last-client-start-cap-v1':
        raise ValueError('STOP: unrecognized window selection strategy')
    if not requests:
        raise ValueError('STOP: no HIGH_LOAD client requests')
    starts = [parsed_time(row['started_at']) for row in requests]
    if any(stamp > phase_ended_at for stamp in starts):
        raise ValueError('STOP: request start beyond actual phase end')
    independently_bounded = min(phase_ended_at, max(starts) + timedelta(seconds=2))
    if meta.get('window_phase_ended_at') != independently_bounded.isoformat():
        raise ValueError('STOP: independently reconstructed window phase end mismatch')
    return independently_bounded


def audit(label: str, slot: int, *, admit: bool = False) -> dict:
    split = collector.split_for(label, slot)
    folder = base.run_dir(label, slot)
    if folder.is_symlink() or not folder.is_dir() or folder.resolve().parent != base.RUNS.resolve():
        raise ValueError('STOP: no normal capture folder for specified slot')
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from scripts.phase19c2d_fault_pilot import parsed_time, correlation_gate, recovery_gate
    intent_file = folder / 'intent.json'
    manifest_file = folder / 'source_manifest.json'
    if intent_file.is_symlink() or manifest_file.is_symlink():
        raise ValueError('STOP: symlink in provenance')
    intent = original.read_json(intent_file)
    manifest = original.read_json(manifest_file)
    run_id = intent.get('run_id')
    if not isinstance(run_id, str) or len(run_id) != 24 or not run_id.startswith('v2train-'):
        raise ValueError('STOP: invalid freshly committed run ID')
    rate = collector.validate_rate(label, intent.get('rps'))
    is_db = label == 'DATABASE_UNAVAILABLE'
    if (intent.get('schema_version') != 'faultweave-v2-training-intent.1'
        or intent.get('purpose') != 'new independent training capture, NOT historical/diagnostic promotion'
        or intent.get('collector') != collector.COLLECTOR
        or intent.get('label') != label or intent.get('slot') != slot
        or intent.get('split') != split or intent.get('scenario_type') != 'known_fault'
        or intent.get('target') != collector.TARGETS[label]
        or intent.get('intensity') != collector.INTENSITY
        or intent.get('duration_seconds') != collector.DURATION
        or intent.get('isolation_project') != (isolated.PROJECT if is_db else None)):
        raise ValueError('STOP: wrong precommitted intent')
    if (manifest.get('schema_version') != 'faultweave-v2-training-capture.1'
        or manifest.get('run_id') != run_id or manifest.get('label') != label
        or manifest.get('split') != split or manifest.get('scenario_type') != 'known_fault'
        or manifest.get('training_eligible') is not True
        or manifest.get('threshold_tuning_eligible') is not (split == 'validation')
        or manifest.get('admission_requires_separate_independent_audit') is not True
        or manifest.get('client_provenance') != 'experiments.runner.execute_plan'
        or manifest.get('outgoing_header') != 'X-Run-ID'
        or manifest.get('window_seconds') != 60
        or manifest.get('intent_sha256') != hashlib.sha256(intent_file.read_bytes()).hexdigest()
        or manifest.get('feature_schema_sha256') != hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest()):
        raise ValueError('STOP: source manifest provenance mismatch')
    required = {'requests.jsonl', 'recovery_requests.jsonl', 'events.jsonl',
                'feature_row.json', 'capture_metadata.json', 'protected_ground_truth.json'}
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, dict) or set(artifacts) != required:
        raise ValueError('STOP: missing/extra declared artifacts')
    for name, info in artifacts.items():
        if not isinstance(info, dict) or type(info.get('bytes')) is not int or type(info.get('sha256')) is not str:
            raise ValueError('STOP: invalid artifact hash declaration')
        original.check_sha(folder/name, info['sha256'], info['bytes'])
    requests = original.jsonl(folder/'requests.jsonl')
    recovery = original.jsonl(folder/'recovery_requests.jsonl')
    events = original.jsonl(folder/'events.jsonl')
    row = original.read_json(folder/'feature_row.json')
    meta = original.read_json(folder/'capture_metadata.json')
    truth = original.read_json(folder/'protected_ground_truth.json')
    if (row.get('schema_version') != 'faultweave-v2-feature-row.1'
        or row.get('run_id') != run_id or row.get('label') != label
        or row.get('split') != split or row.get('scenario_type') != 'known_fault'
        or row.get('interval') != 'fault' or row.get('training_eligible') is not True
        or row.get('threshold_tuning_eligible') is not (split == 'validation')
        or row.get('window_seconds') != 60 or not isinstance(row.get('features'), dict)
        or set(row['features']) != set(FEATURE_NAMES) or len(row['features']) != 65
        or any(type(v) not in (int, float) or not math.isfinite(v)
               for v in row['features'].values())):
        raise ValueError('STOP: frozen numerical feature contract failed')
    if (truth.get('run_id') != run_id or truth.get('fault_id') != label
        or truth.get('target') != collector.TARGETS[label]
        or truth.get('intensity') != collector.INTENSITY
        or truth.get('truth_role') != 'audit_only_not_model_feature'
        or meta.get('collector') != collector.COLLECTOR
        or meta.get('healthy_pre_post') is not True
        or meta.get('scheduled_rps') != rate
        or meta.get('scheduled_requests') != len(requests)
        or len(requests) != round(collector.DURATION * rate)
        or len(recovery) != 8):
        raise ValueError('STOP: measured traffic / truth disagree')
    ids = [r.get('request_id') for r in requests + recovery]
    if len(set(ids)) != len(ids) or any(
        not isinstance(rid, str) or not rid.startswith(run_id+'-request-') for rid in ids
    ) or any(r.get('run_id') != run_id or r.get('scenario') != 'valid'
             or not r.get('trace_id') or not r.get('started_at') for r in requests+recovery
    ) or any(e.get('run_id') != run_id for e in events):
        raise ValueError('STOP: malformed or overlapping request/trace identifiers')
    correlation_gate(requests+recovery, events)
    started, ended = parsed_time(meta['phase_started_at']), parsed_time(meta['phase_ended_at'])
    if not parsed_time(intent['collection_started_at']) <= started < ended:
        raise ValueError('STOP: observation started before intent')
    for x in ('activated_at', 'expires_at', 'cleared_at'):
        if parsed_time(meta['fault_'+x]) != parsed_time(truth[x]):
            raise ValueError('STOP: activation timestamp mismatch')
    watermark = max(parsed_time(e['timestamp']) for e in events)
    if parsed_time(meta['telemetry_watermark']) != watermark:
        raise ValueError('STOP: log watermark disagreement')
    audit_window_phase_end = verified_window_phase_end(meta, label, ended, requests)
    window = select_exact_window(phase_started_at=started, phase_ended_at=audit_window_phase_end,
             telemetry_watermark=watermark, fault_activated_at=truth['activated_at'],
             fault_expires_at=truth['expires_at'], fault_cleared_at=truth['cleared_at'],
             fault_required=True, lead_guard_seconds=2, tail_guard_seconds=2)
    if (manifest.get('window_started_at') != window.started_at.isoformat()
        or manifest.get('window_ended_at') != window.ended_at.isoformat()
        or row.get('window_started_at') != window.started_at.isoformat()
        or row.get('window_ended_at') != window.ended_at.isoformat()):
        raise ValueError('STOP: reconstructed exact window mismatch')
    proof = recovery_gate(recovery, events, cleared_at=parsed_time(truth['cleared_at']))
    if meta.get('recovery') != proof:
        raise ValueError('STOP: recovery recheck mismatch')
    if is_db:
        stop = meta.get('isolated_postgres_stop_proof')
        if (not isinstance(stop, dict) or stop.get('project') != isolated.PROJECT
            or stop.get('docker_running_during_fault') is not False
            or stop.get('primary_healthy_during_fault') is not True
            or not stop.get('container_id') or not stop.get('primary_postgres_id')
            or stop['container_id'] == stop['primary_postgres_id']
            or meta.get('isolation_project') != isolated.PROJECT
            or not parsed_time(stop['stop_issued_at']) <= parsed_time(stop['stop_verified_at']) <= started):
            raise ValueError('STOP: no credible isolated-only outage witness')
        impact = impact_pilot.effect_gate(window, requests, events, label,
                                           stop_proof=stop, reference=None)
    else:
        reference = impact_pilot.load_healthy_reference()
        if meta.get('healthy_reference') != reference or meta.get('isolation_project') is not None:
            raise ValueError('STOP: healthy reference not independently verified')
        impact = impact_pilot.effect_gate(window, requests, events, label,
                                           stop_proof=None, reference=reference)
        # The source gate is separately checked against server-side gateway latency.
        impact = collector.corroborate_server_overload(window, requests, events, reference, impact)
    if impact != meta.get('impact'):
        raise ValueError('STOP: independently reconstructed fault effect differs')
    recomputed = build_observable_features(run_id=run_id, window=window,
                                           requests=requests, events=events,
                                           require_boundary_evidence=True)
    if recomputed != row['features']:
        raise ValueError('STOP: 65-feature independent recalculation disagrees')
    result = {
        'schema_version': 'faultweave-v2-independent-audit.1',
        'run_id': run_id, 'label': label, 'split': split,
        'verdict': 'VALID_V2_TRAINING_RUN', 'independent': True,
        'request_count': len(requests), 'correlated_event_count': len(events),
        'recovery_count': len(recovery), 'window_seconds': 60, 'feature_count': 65,
        'source_manifest_sha256': hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
    }
    if admit:
        base.write_new(folder/'independent_audit.json', base.serial(result))
        receipt = {
            'run_id': run_id, 'label': label, 'split': split,
            'scenario_type': 'known_fault', 'training_admission': True,
            'audit_verdict': result['verdict'],
            'source_manifest': {'path': str(manifest_file.relative_to(base.ROOT)).replace('\\','/'),
                                'sha256': result['source_manifest_sha256']},
            'independent_audit': {'path': str((folder/'independent_audit.json').relative_to(base.ROOT)).replace('\\','/'),
                                   'sha256': hashlib.sha256(base.serial(result)).hexdigest()},
        }
        base.write_new(folder/'admission_receipt.json', base.serial(receipt))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    act = p.add_mutually_exclusive_group(required=True)
    act.add_argument('--check', action='store_true')
    act.add_argument('--admit', action='store_true')
    p.add_argument('--label', choices=collector.LABELS, required=True)
    p.add_argument('--slot', type=int, required=True)
    args = p.parse_args(argv)
    print(json.dumps(audit(args.label, args.slot, admit=args.admit), indent=2))
    print('ADMITTED INDEPENDENT CAPTURE' if args.admit else 'READ ONLY, no admission writes')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, RuntimeError, OSError, TypeError) as exc:
        print('FINAL TWO INDEPENDENT AUDIT STOP:', exc)
        raise SystemExit(2)
