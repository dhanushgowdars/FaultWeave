#!/usr/bin/env python3
"""Opt-in **fresh** v2 training captures; never upgrades earlier diagnostic runs.

Initially supports the two configurations for which we can check all admission
properties independently: NORMAL @1.5rps and payment DOWNSTREAM_ERROR_BURST
@1.5rps/medium. Other classes are blocked, not mislabeled.

`--preview` performs no filesystem changes, Docker calls or requests.
`--capture` creates an immutable intent BEFORE traffic, then writes raw evidence;
only the separate v2 audit can declare the run admissible.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'data/datasets/v2/training-captures'
CONTROL = ROOT / 'data/datasets/v2/control'
LOCK = CONTROL / 'v2-training-active'
SUPERVISOR_LOCK = CONTROL / 'phase19c2k-active'
DURATION = 75
RPS = 1.5
COUNTS = {'NORMAL': (12, 3, 3), 'DOWNSTREAM_ERROR_BURST': (8, 2, 2)}
FAULT = 'DOWNSTREAM_ERROR_BURST'


def serial(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_new(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise RuntimeError('STOP: output is a symlink')
    with path.open('xb') as fd:
        fd.write(raw)


def split_for(label: str, slot: int) -> str:
    if label not in COUNTS:
        raise ValueError('This class has no approved fresh training collector yet')
    if type(slot) is not int or not 1 <= slot <= sum(COUNTS[label]):
        raise ValueError('Slot out of bounds for this known class')
    train, validation, _test = COUNTS[label]
    return 'train' if slot <= train else 'validation' if slot <= train + validation else 'test'


def run_dir(label: str, slot: int) -> Path:
    # Stable slot directory makes accidental post-hoc re-labeling and re-execution fail closed.
    return RUNS / f'{label.lower()}-{slot:02d}'


def clean_lock() -> None:
    if SUPERVISOR_LOCK.exists() or SUPERVISOR_LOCK.is_symlink():
        raise RuntimeError('STOP: diagnostic supervisor holds an unresolved reservation')
    if LOCK.exists() or LOCK.is_symlink():
        raise RuntimeError('STOP: another training capture or unresolved reservation exists')


def ensure_compact_parent() -> None:
    from scripts.faultweave_v2_fasttrack import source_plan
    source_plan(ROOT)  # Immutable 240-run proposal is verified but remains blocked.


def preflight() -> None:
    from scripts.phase19c2c_healthy_pilot import require_clean_local_stack
    clean_lock()
    ensure_compact_parent()
    require_clean_local_stack()


def _created_new_path(label: str, slot: int, run_id: str, split: str) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    CONTROL.mkdir(parents=True, exist_ok=True)
    if RUNS.is_symlink() or CONTROL.is_symlink():
        raise RuntimeError('STOP: unexpected symlink in capture roots')
    LOCK.mkdir(exist_ok=False)
    write_new(LOCK / 'owner.json', serial({'run_id': run_id, 'slot': slot, 'label': label}))
    path = run_dir(label, slot)
    path.mkdir(exist_ok=False)
    write_new(path / 'intent.json', serial({
        'schema_version': 'faultweave-v2-training-intent.1',
        'purpose': 'new independent training capture, NOT historical/diagnostic promotion',
        'run_id': run_id, 'label': label, 'slot': slot, 'split': split,
        'scenario_type': 'normal' if label == 'NORMAL' else 'known_fault',
        'target': None if label == 'NORMAL' else 'payment',
        'intensity': None if label == 'NORMAL' else 'medium',
        'rps': RPS, 'duration_seconds': DURATION,
        'collection_started_at': datetime.now(UTC).isoformat(),
    }))
    return path


def _raw_record(name: str, records: list[dict]) -> tuple[str, bytes]:
    from scripts.phase19c2c_healthy_pilot import jsonl_bytes
    return name, jsonl_bytes(records)


def _emit_run(path: Path, run_id: str, label: str, split: str,
              requests: list[dict], recovery: list[dict], events: list[dict],
              window: object, vector: dict, metadata: dict,
              ground_truth: dict | None) -> None:
    from datasets.final_features import FEATURE_NAMES
    from scripts.phase19c2c_healthy_pilot import jsonl_bytes
    if len(FEATURE_NAMES) != 65 or tuple(vector) != FEATURE_NAMES:
        raise RuntimeError('STOP: frozen feature schema changed')
    # No fault truth in feature vector; intent is held separately from observables.
    row = {'schema_version': 'faultweave-v2-feature-row.1', 'run_id': run_id,
           'split': split, 'scenario_type': 'normal' if label == 'NORMAL' else 'known_fault',
           'training_eligible': True, 'threshold_tuning_eligible': split == 'validation',
           'label': label, 'interval': 'normal' if label == 'NORMAL' else 'fault',
           'window_seconds': 60, 'window_started_at': window.started_at.isoformat(),
           'window_ended_at': window.ended_at.isoformat(), 'features': vector}
    payloads = {
        'requests.jsonl': jsonl_bytes(requests),
        'recovery_requests.jsonl': jsonl_bytes(recovery),
        'events.jsonl': jsonl_bytes(events),
        'feature_row.json': serial(row),
        'capture_metadata.json': serial(metadata),
    }
    if ground_truth is not None:
        payloads['protected_ground_truth.json'] = serial(ground_truth)
    manifest = {
        'schema_version': 'faultweave-v2-training-capture.1', 'run_id': run_id,
        'split': split, 'scenario_type': row['scenario_type'], 'label': label,
        'training_eligible': True, 'threshold_tuning_eligible': row['threshold_tuning_eligible'],
        'window_seconds': 60, 'window_started_at': row['window_started_at'],
        'window_ended_at': row['window_ended_at'],
        'feature_schema_sha256': digest('\n'.join(FEATURE_NAMES).encode()),
        'outgoing_header': 'X-Run-ID', 'client_provenance': 'experiments.runner.execute_plan',
        'admission_requires_separate_independent_audit': True,
        'artifacts': {name: {'sha256': digest(data), 'bytes': len(data)}
                      for name, data in payloads.items()},
        'intent_sha256': digest((path / 'intent.json').read_bytes()),
    }
    # Source manifest declares intended eligibility; it does NOT claim acceptance.
    for name, data in payloads.items():
        write_new(path / name, data)
    write_new(path / 'source_manifest.json', serial(manifest))


async def acquire_observations(label: str, run_id: str) -> tuple:
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from scripts.phase19c2c_healthy_pilot import (
        proven_client_rows, visible_events, validate_correlations, require_clean_local_stack,
    )
    from scripts.phase19c2d_fault_pilot import (
        new_plan, execute_fault_phase, correlation_gate, fault_evidence_gate,
        recovery_gate, parsed_time,
    )
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import PlannedRequest, collect_run_events, execute_plan
    url = 'http://127.0.0.1:18110'
    require_clean_local_stack()
    count = int(round(DURATION * RPS))
    if label == 'NORMAL':
        plan = [PlannedRequest(sequence=i + 1, offset_seconds=i / RPS, scenario='valid',
                               amount_minor=1200 + i % 1000) for i in range(count)]
        started = datetime.now(UTC)
        raw = await execute_plan(plan, url, run_id, 1920, 16)
        ended = datetime.now(UTC)
        require_clean_local_stack()
        requests = proven_client_rows(raw, run_id)
        events = visible_events(collect_run_events(run_id), run_id)
        validate_correlations(requests, events)
        if len(requests) != count:
            raise RuntimeError('Client request count mismatch')
        watermark = max(parsed_time(e['timestamp']) for e in events)
        window = select_exact_window(phase_started_at=started, phase_ended_at=ended,
                                     telemetry_watermark=watermark, fault_required=False,
                                     lead_guard_seconds=2, tail_guard_seconds=2)
        features = build_observable_features(run_id=run_id, window=window,
                                              requests=requests, events=events,
                                              require_boundary_evidence=True)
        return requests, [], events, window, features, {
            'phase_started_at': started.isoformat(), 'phase_ended_at': ended.isoformat(),
            'telemetry_watermark': watermark.isoformat(),
            'healthy_pre_post': True, 'scheduled_requests': count,
            'scheduled_rps': RPS,
        }, None
    if label != FAULT:
        raise RuntimeError('Unsupported label')
    store = FaultStateStore(ROOT / 'data/fault-control')
    raw, started, ended, activation, cleared = await execute_fault_phase(
        store=store, run_id=run_id, plan=new_plan(run_id, RPS), gateway_url=url,
        executor=execute_plan)
    require_clean_local_stack()
    recovery_raw = await execute_plan(new_plan(run_id, RPS, recovery=True),
                                      url, run_id, 1921, 8)
    require_clean_local_stack()
    requests = proven_client_rows(raw, run_id)
    recovery = proven_client_rows(recovery_raw, run_id)
    events = visible_events(collect_run_events(run_id), run_id)
    correlation_gate(requests + recovery, events)
    proof = recovery_gate(recovery, events, cleared_at=cleared)
    watermark = max(parsed_time(e['timestamp']) for e in events)
    window = select_exact_window(phase_started_at=started, phase_ended_at=ended,
                                 telemetry_watermark=watermark,
                                 fault_activated_at=activation.activated_at,
                                 fault_expires_at=activation.expires_at,
                                 fault_cleared_at=cleared, fault_required=True,
                                 lead_guard_seconds=2, tail_guard_seconds=2)
    features = build_observable_features(run_id=run_id, window=window,
                                          requests=requests, events=events,
                                          require_boundary_evidence=True)
    impact = fault_evidence_gate(window, requests, events)
    return requests, recovery, events, window, features, {
        'phase_started_at': started.isoformat(), 'phase_ended_at': ended.isoformat(),
        'telemetry_watermark': watermark.isoformat(),
        'fault_activated_at': parsed_time(activation.activated_at).isoformat(),
        'fault_expires_at': parsed_time(activation.expires_at).isoformat(),
        'fault_cleared_at': cleared.isoformat(),
        'healthy_pre_post': True, 'scheduled_requests': len(new_plan(run_id, RPS)),
        'scheduled_rps': RPS, 'recovery': proof, 'impact': impact,
    }, {
        'run_id': run_id, 'fault_id': FAULT, 'target': 'payment', 'intensity': 'medium',
        'activated_at': parsed_time(activation.activated_at).isoformat(),
        'expires_at': parsed_time(activation.expires_at).isoformat(),
        'cleared_at': cleared.isoformat(),
    }


def release_own_lock(run_id: str) -> None:
    owner = json.loads((LOCK / 'owner.json').read_text())
    if owner.get('run_id') != run_id:
        raise RuntimeError('STOP: reservation owner mismatch; preserve lock')
    (LOCK / 'owner.json').unlink()
    LOCK.rmdir()


async def capture(label: str, slot: int) -> Path:
    split = split_for(label, slot)
    # If slot exists, NEVER repeat it; independent attempts need separate reviewed evidence.
    if run_dir(label, slot).exists() or run_dir(label, slot).is_symlink():
        raise RuntimeError('STOP: this slot has already been attempted; no silent retry')
    preflight()
    run_id = 'v2train-' + uuid4().hex[:16]
    path = _created_new_path(label, slot, run_id, split)
    try:
        rows, recovery, events, window, features, metadata, truth = await acquire_observations(label, run_id)
        _emit_run(path, run_id, label, split, rows, recovery, events, window, features,
                  metadata, truth)
        # A separate process must perform independent audit before admission.
        release_own_lock(run_id)
    except BaseException:
        # Intent and active lock remain for inspection; no automatic rerun or overwrites.
        print('STOP: capture attempt preserved with reservation. Review before resolving.')
        raise
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--preview', action='store_true')
    action.add_argument('--capture', action='store_true')
    parser.add_argument('--label', choices=sorted(COUNTS), required=True)
    parser.add_argument('--slot', type=int, required=True)
    parser.add_argument('--acknowledge-fresh-research-traffic', action='store_true')
    args = parser.parse_args(argv)
    split = split_for(args.label, args.slot)
    if args.preview:
        print(f'PREVIEW ONLY — {args.label} slot {args.slot}, split {split}, '
              f'75s @1.5 RPS, exact 60s; no Docker/network/filesystem changes')
        return 0
    if not args.acknowledge_fresh_research_traffic:
        raise ValueError('Explicit --acknowledge-fresh-research-traffic required')
    result = asyncio.run(capture(args.label, args.slot))
    print('NEW TRAINING CAPTURE EVIDENCE:', result)
    print('NOT ADMITTED until separate independent audit succeeds')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print('V2 TRAINING CAPTURE STOP:', exc)
        raise SystemExit(2)
