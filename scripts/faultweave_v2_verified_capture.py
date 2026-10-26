#!/usr/bin/env python3
"""Five provenance-checked NEW research capture classes. Never promotes old pilots.

Each run owns an exclusive pre-registered slot, uses the measured 1.5 RPS
75-second live workload and retains the original diagnostic effect gates.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scripts import faultweave_v2_capture as base

ROOT, RPS, DURATION = base.ROOT, base.RPS, base.DURATION
RUNS, LOCK = base.RUNS, base.LOCK
SPECS = {
    'DOWNSTREAM_TIMEOUT': ('payment->ledger', 'medium', 'timeout'),
    'DATABASE_HIGH_LATENCY': ('payment', 'medium', 'service'),
    'SERVICE_UNAVAILABLE': ('payment', 'medium', 'service'),
    'CONNECTION_POOL_EXHAUSTION': ('payment', 'high', 'resource'),
    'DATABASE_LOCK_CONTENTION': ('payment', 'medium', 'resource'),
}
COUNTS = (8, 2, 2)


def spec(label: str) -> tuple[str, str, str]:
    if label not in SPECS:
        raise ValueError('STOP: fault family is not independently validated for NEW training')
    return SPECS[label]


def split_for(label: str, slot: int) -> str:
    spec(label)
    if type(slot) is not int or not 1 <= slot <= 12:
        raise ValueError('STOP: slot must be 1..12')
    return 'train' if slot <= 8 else 'validation' if slot <= 10 else 'test'


def slot_path(label: str, slot: int) -> Path:
    return base.run_dir(label, slot)


def create_intent(label: str, slot: int, run_id: str) -> Path:
    target, intensity, mechanism = spec(label)
    split = split_for(label, slot)
    base.clean_lock()
    base.ensure_compact_parent()
    path = slot_path(label, slot)
    if path.exists() or path.is_symlink():
        raise RuntimeError('STOP: attempted training slot exists; never silently retry or overwrite')
    RUNS.mkdir(parents=True, exist_ok=True)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if RUNS.is_symlink() or LOCK.parent.is_symlink():
        raise RuntimeError('STOP: capture directory is a symlink')
    LOCK.mkdir(exist_ok=False)
    base.write_new(LOCK / 'owner.json', base.serial({'run_id': run_id, 'slot': slot, 'label': label}))
    path.mkdir(exist_ok=False)
    base.write_new(path / 'intent.json', base.serial({
        'schema_version': 'faultweave-v2-training-intent.1',
        'purpose': 'new independent training capture, NOT historical/diagnostic promotion',
        'run_id': run_id, 'label': label, 'slot': slot, 'split': split,
        'scenario_type': 'known_fault', 'target': target, 'intensity': intensity,
        'rps': RPS, 'duration_seconds': DURATION,
        'collection_started_at': datetime.now(UTC).isoformat(),
        'collector': 'verified-five.1', 'mechanism': mechanism,
    }))
    return path


def emit(path: Path, run_id: str, label: str, split: str, *, requests: list[dict],
         recovery: list[dict], events: list[dict], window: object, vector: dict,
         metadata: dict, truth: dict, db_samples: list[dict] | None) -> None:
    from datasets.final_features import FEATURE_NAMES
    from scripts.phase19c2c_healthy_pilot import jsonl_bytes
    if len(FEATURE_NAMES) != 65 or tuple(vector) != FEATURE_NAMES:
        raise RuntimeError('STOP: frozen 65-feature schema/order changed')
    row = {'schema_version': 'faultweave-v2-feature-row.1', 'run_id': run_id,
           'split': split, 'scenario_type': 'known_fault',
           'training_eligible': True, 'threshold_tuning_eligible': split == 'validation',
           'label': label, 'interval': 'fault', 'window_seconds': 60,
           'window_started_at': window.started_at.isoformat(),
           'window_ended_at': window.ended_at.isoformat(), 'features': vector}
    artifacts = {
        'requests.jsonl': jsonl_bytes(requests),
        'recovery_requests.jsonl': jsonl_bytes(recovery),
        'events.jsonl': jsonl_bytes(events),
        'feature_row.json': base.serial(row),
        'capture_metadata.json': base.serial(metadata),
        'protected_ground_truth.json': base.serial(truth),
    }
    if db_samples is not None:
        if not db_samples:
            raise RuntimeError('STOP: resource proof has no physical PostgreSQL samples')
        artifacts['db_samples.jsonl'] = jsonl_bytes(db_samples)
    manifest = {
        'schema_version': 'faultweave-v2-training-capture.1',
        'run_id': run_id, 'label': label, 'split': split, 'scenario_type': 'known_fault',
        'training_eligible': True, 'threshold_tuning_eligible': split == 'validation',
        'window_seconds': 60, 'window_started_at': row['window_started_at'],
        'window_ended_at': row['window_ended_at'],
        'feature_schema_sha256': base.digest('\n'.join(FEATURE_NAMES).encode()),
        'outgoing_header': 'X-Run-ID',
        'client_provenance': 'experiments.runner.execute_plan',
        'admission_requires_separate_independent_audit': True,
        'artifacts': {name: {'sha256': base.digest(data), 'bytes': len(data)}
                      for name, data in artifacts.items()},
        'intent_sha256': base.digest((path / 'intent.json').read_bytes()),
    }
    for name, payload in artifacts.items():
        base.write_new(path / name, payload)
    base.write_new(path / 'source_manifest.json', base.serial(manifest))


async def observations(label: str, run_id: str):
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from scripts.phase19c2c_healthy_pilot import (require_clean_local_stack,
        proven_client_rows, visible_events)
    from scripts.phase19c2d_fault_pilot import (new_plan, parsed_time, correlation_gate,
        recovery_gate)
    from scripts import phase19c2j_timeout_pilot as timepilot
    from scripts import phase19c2m_service_effect_pilot as servicepilot
    from scripts import phase19c2n_resource_pilot as resourcepilot
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import execute_plan, collect_run_events

    target, intensity, mechanism = spec(label)
    url = 'http://127.0.0.1:18110'
    store = FaultStateStore(ROOT / 'data/fault-control')
    require_clean_local_stack()  # own training reservation is already held
    baseline, samples = None, None
    if mechanism == 'resource':
        baseline = resourcepilot.ensure_live_preflight(label)
    plan = new_plan(run_id, RPS)
    if mechanism == 'timeout':
        raw, started, ended, activation, cleared = await timepilot.execute_bounded_timeout(
            store, run_id, target, plan, url, execute_plan)
    elif mechanism == 'service':
        raw, started, ended, activation, cleared = await servicepilot.execute_fault_phase(
            store, run_id, label, target, plan, url, execute_plan)
    else:
        # Same bounded 15-connection/one-worker limit and physical DB telemetry
        # as the independently proven resource diagnostic.
        db_user = resourcepilot.command('exec', '-T', 'postgres', 'printenv', 'POSTGRES_USER')
        db_name = resourcepilot.command('exec', '-T', 'postgres', 'printenv', 'POSTGRES_DB')
        activation = store.acquire(run_id=run_id, fault_id=label, target=target,
                                   intensity=intensity, duration_seconds=resourcepilot.LEASE_SECONDS)
        samples = []
        stop = asyncio.Event()
        monitor = asyncio.create_task(resourcepilot.monitor_samples(stop, samples, db_user, db_name))
        try:
            started = datetime.now(UTC)
            raw = await execute_plan(plan, url, run_id, 1913, 16)
            ended = datetime.now(UTC)
            if ended >= parsed_time(activation.expires_at):
                raise RuntimeError('STOP: resource fault lease expired during observations')
        finally:
            store.release(activation.activation_id)
            cleared = datetime.now(UTC)
            stop.set()
            await monitor

    # Recheck before recovery; resource failures may need time to drain.
    healthy = False
    for _ in range(35):
        try:
            await asyncio.to_thread(require_clean_local_stack)
            healthy = True
            break
        except RuntimeError:
            await asyncio.sleep(1)
    if not healthy:
        raise RuntimeError('STOP: stack did not recover after fault lease release')
    recovered_raw = await execute_plan(new_plan(run_id, RPS, recovery=True),
                                       url, run_id, 1914, 8)
    require_clean_local_stack()
    requests = proven_client_rows(raw, run_id)
    recovery = proven_client_rows(recovered_raw, run_id)
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
    vector = build_observable_features(run_id=run_id, window=window,
                                       requests=requests, events=events,
                                       require_boundary_evidence=True)
    if mechanism == 'timeout':
        effect = timepilot.timeout_impact(window, requests, events, target)
    elif mechanism == 'service':
        effect = servicepilot.observed_impact(window, requests, events, label, target)
    else:
        effect = resourcepilot.effect_gate(window, requests, events, samples, label, baseline)
    metadata = {
        'phase_started_at': started.isoformat(), 'phase_ended_at': ended.isoformat(),
        'telemetry_watermark': watermark.isoformat(),
        'fault_activated_at': parsed_time(activation.activated_at).isoformat(),
        'fault_expires_at': parsed_time(activation.expires_at).isoformat(),
        'fault_cleared_at': cleared.isoformat(),
        'healthy_pre_post': True, 'scheduled_requests': len(plan), 'scheduled_rps': RPS,
        'impact': effect, 'recovery': proof, 'mechanism': mechanism,
    }
    if baseline is not None:
        metadata['preflight'] = baseline
    truth = {
        'run_id': run_id, 'fault_id': label, 'target': target,
        'intensity': intensity,
        'activated_at': parsed_time(activation.activated_at).isoformat(),
        'expires_at': parsed_time(activation.expires_at).isoformat(),
        'cleared_at': cleared.isoformat(),
        'truth_role': 'audit_only_not_model_feature',
    }
    return dict(requests=requests, recovery=recovery, events=events, window=window,
                vector=vector, metadata=metadata, truth=truth, db_samples=samples)


async def capture(label: str, slot: int) -> Path:
    split_for(label, slot)
    if slot_path(label, slot).exists() or slot_path(label, slot).is_symlink():
        raise RuntimeError('STOP: slot already attempted; no reuse')
    base.preflight()
    if spec(label)[2] == 'resource':
        # Physical budget gate before opening a journal; repeated inside observation.
        from scripts.phase19c2n_resource_pilot import ensure_live_preflight
        ensure_live_preflight(label)
    run_id = 'v2train-' + uuid4().hex[:16]
    path = create_intent(label, slot, run_id)
    try:
        data = await observations(label, run_id)
        emit(path, run_id, label, split_for(label, slot), **data)
        base.release_own_lock(run_id)
    except BaseException:
        print('STOP: journal and reservation preserved; investigate, no automatic retry')
        raise
    return path


def batch(label: str, first: int, count: int, *, db_ack: bool) -> None:
    if not 1 <= count <= 10 or first + count - 1 > 12:
        raise ValueError('STOP: batch must be 1..10 unused slots within 1..12')
    target, intensity, mechanism = spec(label)
    if mechanism == 'resource' and not db_ack:
        raise ValueError('STOP: resource pressure requires --acknowledge-db-pressure')
    for slot in range(first, first + count):
        print(f'BEGIN {label} SLOT {slot}', flush=True)
        asyncio.run(capture(label, slot))
        for mode in ('--check', '--admit'):
            cmd = [sys.executable, '-m', 'scripts.faultweave_v2_verified_audit',
                   mode, '--label', label, '--slot', str(slot)]
            completed = subprocess.run(cmd, cwd=ROOT, check=False)
            if completed.returncode:
                raise RuntimeError(f'STOP: independent {mode} failed: {label} slot {slot}')
        print(f'ADMITTED {label} SLOT {slot}', flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--preview', action='store_true')
    g.add_argument('--batch', action='store_true')
    p.add_argument('--label', required=True)
    p.add_argument('--start-slot', type=int, default=1)
    p.add_argument('--count', type=int, default=1)
    p.add_argument('--acknowledge-fresh-research-traffic', action='store_true')
    p.add_argument('--acknowledge-db-pressure', action='store_true')
    a = p.parse_args(argv)
    target, intensity, mechanism = spec(a.label)
    if not 1 <= a.count <= 10 or a.start_slot < 1 or a.start_slot + a.count - 1 > 12:
        raise ValueError('STOP: slots must fit 1..12 and count <=10')
    if a.preview:
        print(f'PREVIEW {a.label}: target={target}, intensity={intensity}, '
              f'slots={a.start_slot}..{a.start_slot+a.count-1}, '
              f'75s@1.5rps, 60s exact, mode={mechanism}; NO mutations')
        return 0
    if not a.acknowledge_fresh_research_traffic:
        raise ValueError('STOP: explicit research traffic acknowledgement required')
    batch(a.label, a.start_slot, a.count, db_ack=a.acknowledge_db_pressure)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print('V2 VERIFIED CAPTURE STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
