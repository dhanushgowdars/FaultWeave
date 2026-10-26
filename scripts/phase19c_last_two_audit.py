#!/usr/bin/env python3
"""Independent read-only validation of a last-two known-fault research pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2d_fault_pilot import correlation_gate, parsed_time, recovery_gate
from scripts.phase19c_last_two_pilot import SCHEMA, effect_gate, in_window

ARTIFACTS = {'requests.jsonl', 'recovery_requests.jsonl', 'events.jsonl',
             'candidate_feature.json', 'protected_ground_truth.json'}
REQUEST_FIELDS = {'run_id','request_id','trace_id','started_at','latency_ms',
                  'status_code','expected_outcome','transport_error','scenario'}
EVENT_FIELDS = {'run_id','request_id','trace_id','timestamp','service','event_type','path',
                'downstream_service','latency_ms','success','level','status_code','error_type'}


def json_file(path):
    obj = json.loads(path.read_text(encoding='utf-8'),
                     parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(obj, dict):
        raise ValueError(f'Expected object: {path}')
    return obj


def jsonl(path):
    values = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                obj = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(obj, dict):
                    raise ValueError(f'Expected JSONL object: {path}')
                values.append(obj)
    return values


def audit(root: Path, pilot: Path) -> int:
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import build_observable_features, select_exact_window

    root = root.resolve(strict=True)
    pilot = pilot.resolve(strict=True)
    if (pilot.parent != (root / 'data/datasets/v2/pilot-fault').resolve() or
        not re.fullmatch(r'v2finaltwo-[0-9a-f]{16}', pilot.name)):
        raise ValueError('Untrusted diagnostic directory')
    manifest = json_file(pilot/'manifest.json')
    candidate = json_file(pilot/'candidate_feature.json')
    truth = json_file(pilot/'protected_ground_truth.json')
    if not all(x.get('run_id') == pilot.name and x.get('schema_version') == SCHEMA
               for x in (manifest, candidate, truth)):
        raise ValueError('Mismatched run/schema identifiers')
    if any(x.get('training_eligible') is not False or x.get('split') != 'diagnostic_only'
           or x.get('threshold_tuning_eligible') is not False for x in (manifest,candidate)):
        raise ValueError('Diagnostic data incorrectly admitted into training')
    if candidate.get('label') is not None or candidate.get('candidate_status') != 'UNLABELED_LAST_TWO_DIAGNOSTIC':
        raise ValueError('Candidate contains a fault label or unapproved state')
    if candidate.get('window_seconds') != 60 or candidate.get('feature_count') != 65:
        raise ValueError('Feature/window contract invalid')
    if (manifest.get('outgoing_header') != 'X-Run-ID' or
        manifest.get('feature_schema_sha256') != hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest()):
        raise ValueError('Telemetry/feature provenance contract invalid')
    if set(manifest.get('artifacts', {})) != ARTIFACTS:
        raise ValueError('Unexpected artifact inventory')
    for name in ARTIFACTS:
        data = (pilot/name).read_bytes()
        item = manifest['artifacts'][name]
        if hashlib.sha256(data).hexdigest() != item.get('sha256') or len(data) != item.get('bytes'):
            raise ValueError(f'Artifact checksum/size mismatch: {name}')
    requests = jsonl(pilot/'requests.jsonl')
    recovery = jsonl(pilot/'recovery_requests.jsonl')
    events = jsonl(pilot/'events.jsonl')
    if not requests or not recovery or not events:
        raise ValueError('Missing real observations')
    if any(set(r) - REQUEST_FIELDS or r.get('run_id') != pilot.name or r.get('scenario') != 'valid'
           for r in requests + recovery):
        raise ValueError('Unallowlisted or mixed-run client observation')
    if any(set(e) - EVENT_FIELDS or e.get('run_id') != pilot.name for e in events):
        raise ValueError('Unallowlisted or mixed-run event')
    ids = [r.get('request_id') for r in requests + recovery]
    if (len(set(ids)) != len(ids) or
        any(not isinstance(i,str) or not i.startswith(pilot.name+'-request-') for i in ids)):
        raise ValueError('Duplicate/missing request identity')
    correlation_gate(requests + recovery, events)
    meta = manifest.get('metadata') or {}
    if meta.get('source_script') != 'scripts/phase19c_last_two_pilot.py':
        raise ValueError('Unrecognized collector source')
    if meta.get('observed_requests') != len(requests) or meta.get('observed_events') != len(events):
        raise ValueError('Mismatched observation counts')
    if meta.get('passed_pre_post_stack_health') is not True:
        raise ValueError('Post-run health not established')
    if truth.get('fault_id') not in ('DATABASE_UNAVAILABLE','HIGH_LOAD'):
        raise ValueError('Unsupported protected fault family')
    target = 'postgresql' if truth['fault_id'] == 'DATABASE_UNAVAILABLE' else 'gateway'
    if truth.get('target') != target or truth.get('intensity') != 'mild':
        raise ValueError('Unsupported configuration')
    if (truth['fault_id'] == 'HIGH_LOAD') != (meta.get('healthy_reference') is not None):
        raise ValueError('Missing/invalid load baseline reference')
    if truth['fault_id'] == 'HIGH_LOAD':
        from scripts.phase19c_last_two_pilot import load_healthy_reference
        if meta['healthy_reference'] != load_healthy_reference():
            raise ValueError('Saved high-load baseline differs from the verified healthy pilot')
    window = select_exact_window(phase_started_at=meta['phase_started_at'],
                                 phase_ended_at=meta['phase_ended_at'],
                                 telemetry_watermark=meta['telemetry_watermark'],
                                 fault_activated_at=truth['activated_at'],
                                 fault_expires_at=truth['expires_at'],
                                 fault_cleared_at=truth['cleared_at'],
                                 fault_required=True, lead_guard_seconds=2, tail_guard_seconds=2)
    if (window.started_at != parsed_time(candidate['window_started_at']) or
        window.ended_at != parsed_time(candidate['window_ended_at']) or
        window.started_at != parsed_time(manifest['window_started_at']) or
        window.ended_at != parsed_time(manifest['window_ended_at'])):
        raise ValueError('Stored exact window does not match independently calculated window')
    vector = build_observable_features(run_id=pilot.name, window=window,
                                       requests=requests, events=events,
                                       require_boundary_evidence=True)
    if tuple(vector) != FEATURE_NAMES or candidate.get('features') != vector:
        raise ValueError('65-feature parity not reproduced')
    effect = effect_gate(window, requests, events, truth['fault_id'],
                         stop_proof=truth.get('physical_outage'),
                         reference=meta.get('healthy_reference'))
    recovered = recovery_gate(recovery, events, cleared_at=parsed_time(truth['cleared_at']))
    if effect != meta.get('impact') or recovered != meta.get('recovery'):
        raise ValueError('Effect/recovery disagree with saved evidence')
    print('LAST TWO AUDIT: 5/5 SHA256 verified; exact 60s; features 65/65')
    print('FAULT:', truth['fault_id'], 'RUN:', pilot.name)
    print('EFFECT:', effect)
    print('RECOVERY:', recovered)
    print('VERDICT: VALID_LAST_TWO_DIAGNOSTIC — NOT TRAINING ELIGIBLE')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path.cwd())
    parser.add_argument('--pilot-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    return audit(args.project_root, args.pilot_dir)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, KeyError, OSError, TypeError, ImportError) as exc:
        print('LAST TWO AUDIT STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
