#!/usr/bin/env python3
"""Fail-closed, NEW FaultWeave v2 captures for final two known fault families.

HIGH_LOAD: requires independently measured response degradation, not RPS alone.
DATABASE_UNAVAILABLE: only stops an explicitly verified ISOLATED Compose Postgres.
Never stops or resets primary PostgreSQL; all 102 previous captures preserved.
First calibrate HIGH_LOAD without reserving a training slot.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from scripts import faultweave_v2_capture as base
from scripts import faultweave_v2_verified_capture as verified
from scripts import faultweave_v2_isolated_db as isolated
from scripts import phase19c_last_two_pilot as impact_pilot

LABELS = ('HIGH_LOAD', 'DATABASE_UNAVAILABLE')
HIGH_LOAD_RATES = (8.0, 10.0, 12.0, 16.0)
DURATION = 75
TARGETS = {'HIGH_LOAD': 'gateway', 'DATABASE_UNAVAILABLE': 'postgresql'}
INTENSITY = 'mild'
COLLECTOR = 'final-two-isolated.1'


def split_for(label: str, slot: int) -> str:
    if label not in LABELS or type(slot) is not int or not 1 <= slot <= 12:
        raise ValueError('STOP: unsupported label or slot')
    return 'train' if slot <= 8 else 'validation' if slot <= 10 else 'test'


def validate_rate(label: str, rate: float) -> float:
    if label not in LABELS or type(rate) not in (int, float):
        raise ValueError('STOP: invalid label or rate')
    allowed = (1.5,) if label == 'DATABASE_UNAVAILABLE' else HIGH_LOAD_RATES
    if float(rate) not in allowed:
        raise ValueError(f'STOP: approved {label} rates are {allowed}')
    return float(rate)


def create_intent(label: str, slot: int, rate: float, run_id: str) -> Path:
    split = split_for(label, slot)
    base.clean_lock()
    base.ensure_compact_parent()
    path = base.run_dir(label, slot)
    if path.exists() or path.is_symlink():
        raise RuntimeError('STOP: slot already attempted; no reuse or overwrite')
    base.RUNS.mkdir(parents=True, exist_ok=True)
    base.LOCK.parent.mkdir(parents=True, exist_ok=True)
    if base.RUNS.is_symlink() or base.LOCK.parent.is_symlink():
        raise RuntimeError('STOP: unexpected symlink')
    base.LOCK.mkdir(exist_ok=False)
    base.write_new(base.LOCK / 'owner.json', base.serial(
        {'run_id': run_id, 'slot': slot, 'label': label}))
    path.mkdir(exist_ok=False)
    base.write_new(path / 'intent.json', base.serial({
        'schema_version': 'faultweave-v2-training-intent.1',
        'purpose': 'new independent training capture, NOT historical/diagnostic promotion',
        'run_id': run_id, 'label': label, 'slot': slot, 'split': split,
        'scenario_type': 'known_fault', 'target': TARGETS[label],
        'intensity': INTENSITY, 'rps': rate, 'duration_seconds': DURATION,
        'collection_started_at': datetime.now(UTC).isoformat(),
        'collector': COLLECTOR,
        'isolation_project': isolated.PROJECT if label == 'DATABASE_UNAVAILABLE' else None,
    }))
    return path


def raw_events_for(run_id: str, isolated_mode: bool):
    from experiments.runner import collect_run_events
    if not isolated_mode:
        return collect_run_events(run_id)
    from scripts.collect_logs import extract_event
    stdout = isolated.command('logs', '--no-color', '--since', '10m', timeout=60)
    return sorted((event for line in stdout.splitlines()
                   if (event := extract_event(line)) and event.get('run_id') == run_id),
                  key=lambda e: e['timestamp'])


def run_command(*args: str, timeout: int = 25) -> str:
    cmd = subprocess.run(['docker', *args], cwd=base.ROOT, text=True,
                         capture_output=True, timeout=timeout, check=False)
    if cmd.returncode:
        raise RuntimeError('STOP: docker inspect failed: ' + (cmd.stderr or cmd.stdout)[:250])
    return cmd.stdout.strip()


def primary_db_healthy() -> bool:
    original = run_command('compose', 'ps', '-q', 'postgres')
    if not original:
        raise RuntimeError('STOP: original PostgreSQL identity missing')
    return run_command('inspect', '--format', '{{.State.Health.Status}}', original) == 'healthy'


def isolated_stop_proof(container: str) -> dict:
    ids = isolated.inspect_healthy()
    if ids['postgres'] != container:
        raise RuntimeError('STOP: isolated Postgres container changed')
    original = run_command('compose', 'ps', '-q', 'postgres')
    if not original or original == container:
        raise RuntimeError('STOP: original and isolated PostgreSQL not distinct')
    issued = datetime.now(UTC).isoformat()
    # The one Docker stop command is scoped to project faultweave-v2-isodb.
    isolated.command('stop', '--timeout', '10', 'postgres', timeout=45)
    stopped = run_command('inspect', '--format', '{{.State.Running}}', container)
    if stopped != 'false' or not primary_db_healthy():
        raise RuntimeError('STOP: isolated outage unproven or original DB unhealthy')
    return {'project': isolated.PROJECT, 'container_id': container,
            'primary_postgres_id': original, 'docker_running_during_fault': False,
            'primary_healthy_during_fault': True, 'stop_issued_at': issued,
            'stop_verified_at': datetime.now(UTC).isoformat()}


def corroborate_server_overload(window, requests: list[dict], events: list[dict],
                                reference: dict, impact: dict) -> dict:
    """Disallow client-side semaphore queuing alone as evidence of server overload."""
    from scripts.phase19c2d_fault_pilot import parsed_time
    selected = {r['request_id']: r for r in requests
                if window.started_at < parsed_time(r['started_at']) <= window.ended_at}
    server = sorted(float(e['latency_ms']) for e in events
                    if e.get('service') == 'gateway'
                    and e.get('event_type') == 'http_request_completed'
                    and e.get('request_id') in selected
                    and e.get('trace_id') == selected[e['request_id']]['trace_id']
                    and type(e.get('latency_ms')) in (int, float)
                    and window.started_at < parsed_time(e['timestamp']) <= window.ended_at)
    if len(server) < 300:
        raise RuntimeError('STOP: too few timed gateway events to verify actual server overload')
    p95 = server[min(len(server)-1, int(.95*len(server)))]
    failing = int(impact.get('correlated_failure_clients', 0))
    if failing < 10 and not (p95 >= 700 and p95 >= 1.8*reference['reference_p95_ms']):
        raise RuntimeError('STOP: CLIENT_QUEUING_ONLY_NOT_SERVER_OVERLOAD: '
                           f'gateway_p95={p95:.1f}ms, corroborated_failures={failing}')
    return {**impact, 'gateway_server_p95_ms': round(p95, 3),
            'gateway_timed_request_count': len(server),
            'server_side_overload_confirmed': True}


async def collect_correlation_checked_events(
    run_id: str, isolated_mode: bool, requests: list[dict], recovery: list[dict]
) -> list[dict]:
    """Wait briefly for late Docker logs; never invent or skip correlated events.

    Unresolved missing IDs reveal the known client outcome so pre-gateway
    transport failures can be distinguished from missing structured telemetry.
    """
    from scripts.phase19c2c_healthy_pilot import visible_events

    expected = {row['request_id']: row for row in requests + recovery}
    if not expected or len(expected) != len(requests) + len(recovery):
        raise RuntimeError('STOP: missing/duplicate request IDs in observation')
    missing = set(expected)
    events: list[dict] = []
    for attempt in range(4):  # one fetch plus up to three 1-second retries
        events = visible_events(raw_events_for(run_id, isolated_mode), run_id)
        observed = {event.get('request_id') for event in events}
        missing = set(expected) - observed
        if not missing:
            return events
        if attempt < 3:
            await asyncio.sleep(1.0)

    detail = []
    for request_id in sorted(missing)[:3]:
        row = expected[request_id]
        detail.append(
            f"id={request_id} status={row.get('status_code')!r} "
            f"transport_error={row.get('transport_error')!r} "
            f"client_latency_ms={row.get('latency_ms')!r} "
            f"expected_outcome={row.get('expected_outcome')!r}"
        )
    raise RuntimeError(
        f"STOP: MISSING_SERVICE_EVIDENCE_AFTER_4_FETCHES "
        f"missing={len(missing)}/{len(expected)}; " + '; '.join(detail)
        + '; capture NOT admitted, no correlation gate bypassed'
    )


def high_load_window_phase_end(phase_ended_at: datetime, requests: list[dict]) -> datetime:
    """Bound the HIGH_LOAD window by actual request starts, not queue-drain time.

    Records are never padded or synthesized. The true all-responses-complete
    timestamp remains separately recorded in capture_metadata.phase_ended_at.
    """
    from scripts.phase19c2d_fault_pilot import parsed_time
    if not requests:
        raise RuntimeError('STOP: no genuine HIGH_LOAD client observations')
    starts = [parsed_time(row['started_at']) for row in requests]
    if any(stamp > phase_ended_at for stamp in starts):
        raise RuntimeError('STOP: client observation starts after phase end')
    # The 2s is the existing tail guard; never widen actual phase boundaries.
    return min(phase_ended_at, max(starts) + timedelta(seconds=2))


async def observe(label: str, rate: float, run_id: str) -> dict:
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import execute_plan
    from experiments.runner import PlannedRequest
    from scripts.phase19c2c_healthy_pilot import (
        require_clean_local_stack, proven_client_rows, visible_events)
    from scripts.phase19c2d_fault_pilot import (
        new_plan, correlation_gate, recovery_gate, parsed_time)
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features

    is_db = label == 'DATABASE_UNAVAILABLE'
    gateway = 'http://127.0.0.1:28110' if is_db else 'http://127.0.0.1:18110'
    if is_db:
        ids = isolated.inspect_healthy()
        if not primary_db_healthy():
            raise RuntimeError('STOP: original PostgreSQL is not healthy')
    else:
        require_clean_local_stack()
        ids = None
    healthy = impact_pilot.load_healthy_reference() if not is_db else None
    store = FaultStateStore((isolated.FIXTURE if is_db else base.ROOT / 'data') / 'fault-control')
    activation = store.acquire(run_id=run_id, fault_id=label,
                               target=TARGETS[label], intensity=INTENSITY,
                               duration_seconds=145)
    stopped = False
    stop_proof = None
    try:
        if is_db:
            # set flag first so that restoration is attempted even on stop error
            stopped = True
            stop_proof = isolated_stop_proof(ids['postgres'])
        started = datetime.now(UTC)
        raw = await asyncio.wait_for(execute_plan(new_plan(run_id, rate), gateway,
                                                  run_id, 1961, 32), timeout=110)
        ended = datetime.now(UTC)
        if ended >= parsed_time(activation.expires_at):
            raise RuntimeError('STOP: 145s fault lease expired')
    finally:
        try:
            if stopped:
                # Scoped restoration, NEVER docker compose up on primary project.
                isolated.command('up', '-d', '--no-deps', '--wait', 'postgres', timeout=180)
        finally:
            store.release(activation.activation_id)
        cleared = datetime.now(UTC)
    for _ in range(60):
        try:
            if is_db:
                isolated.inspect_healthy()
            else:
                require_clean_local_stack()
            break
        except RuntimeError:
            await asyncio.sleep(1)
    else:
        raise RuntimeError('STOP: experiment stack did not recover')
    recovery_plan = [PlannedRequest(sequence=10001 + i, offset_seconds=i / 1.5,
                                    scenario='valid', amount_minor=4000+i)
                     for i in range(8)]
    recovery_raw = await execute_plan(recovery_plan, gateway, run_id, 1962, 8)
    if is_db:
        isolated.inspect_healthy()
        if not primary_db_healthy():
            raise RuntimeError('STOP: primary PostgreSQL health lost')
    else:
        require_clean_local_stack()
    requests = proven_client_rows(raw, run_id)
    recovery = proven_client_rows(recovery_raw, run_id)
    events = await collect_correlation_checked_events(run_id, is_db, requests, recovery)
    correlation_gate(requests+recovery, events)
    recovery_proof = recovery_gate(recovery, events, cleared_at=cleared)
    watermark = max(parsed_time(e['timestamp']) for e in events)
    window_phase_end = high_load_window_phase_end(ended, requests) if not is_db else ended
    window = select_exact_window(
        phase_started_at=started, phase_ended_at=window_phase_end,
        telemetry_watermark=watermark,
        fault_activated_at=activation.activated_at,
        fault_expires_at=activation.expires_at,
        fault_cleared_at=cleared,
        fault_required=True, lead_guard_seconds=2, tail_guard_seconds=2)
    vector = build_observable_features(run_id=run_id, window=window,
                                       requests=requests, events=events,
                                       require_boundary_evidence=True)
    impact = impact_pilot.effect_gate(window, requests, events, label,
                                      stop_proof=stop_proof, reference=healthy)
    if not is_db:
        impact = corroborate_server_overload(window, requests, events, healthy, impact)
    metadata = {
        'phase_started_at': started.isoformat(), 'phase_ended_at': ended.isoformat(),
        'telemetry_watermark': watermark.isoformat(), 'scheduled_rps': rate,
        'scheduled_requests': len(requests),
        'fault_activated_at': parsed_time(activation.activated_at).isoformat(),
        'fault_expires_at': parsed_time(activation.expires_at).isoformat(),
        'fault_cleared_at': cleared.isoformat(),
        'healthy_pre_post': True, 'recovery': recovery_proof,
        'impact': impact, 'healthy_reference': healthy,
        'isolated_postgres_stop_proof': stop_proof,
        'isolation_project': isolated.PROJECT if is_db else None,
        'collector': COLLECTOR,
    }
    if not is_db:
        metadata['window_selection_strategy'] = 'high-load-last-client-start-cap-v1'
        metadata['window_phase_ended_at'] = window_phase_end.isoformat()
    truth = {
        'run_id': run_id, 'fault_id': label, 'target': TARGETS[label],
        'intensity': INTENSITY,
        'activated_at': parsed_time(activation.activated_at).isoformat(),
        'expires_at': parsed_time(activation.expires_at).isoformat(),
        'cleared_at': cleared.isoformat(),
        'truth_role': 'audit_only_not_model_feature',
    }
    return dict(requests=requests, recovery=recovery, events=events,
                window=window, vector=vector, metadata=metadata, truth=truth)


async def one_capture(label: str, slot: int, rate: float) -> None:
    if base.run_dir(label, slot).exists():
        raise RuntimeError('STOP: slot already exists; never overwrite')
    base.preflight()
    if label == 'DATABASE_UNAVAILABLE':
        isolated.inspect_healthy()
    run_id = 'v2train-' + uuid4().hex[:16]
    path = create_intent(label, slot, rate, run_id)
    try:
        data = await observe(label, rate, run_id)
        verified.emit(path, run_id, label, split_for(label, slot),
                      db_samples=None, **data)
        base.release_own_lock(run_id)
    except BaseException:
        print('STOP: attempted slot/evidence preserved; do not retry automatically')
        raise
    for flag in ('--check', '--admit'):
        outcome = subprocess.run([sys.executable, '-m',
                                  'scripts.faultweave_v2_final_two_audit', flag,
                                  '--label', label, '--slot', str(slot)],
                                 cwd=base.ROOT, check=False)
        if outcome.returncode != 0:
            raise RuntimeError(f'STOP: independent {flag} failed; preserve slot')
    print(f'ADMITTED {label} SLOT {slot}')


async def calibrate_high_load(rate: float) -> None:
    base.preflight()
    run_id = 'v2pilot-' + uuid4().hex[:16]
    print(f'CALIBRATION ONLY, no slot reservation: HIGH_LOAD at {rate:g} RPS')
    try:
        result = await observe('HIGH_LOAD', rate, run_id)
    except Exception as exc:
        raise RuntimeError('NOT VERIFIED AS OVERLOAD: ' + str(exc)) from exc
    print('OBSERVED OVERLOAD EVIDENCE:', json.dumps(result['metadata']['impact'], indent=2))
    print('CALIBRATION PASSED, NOT ADMITTED. Use this RPS for ONE slot-1 test.')


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true')
    mode.add_argument('--calibrate-high-load', action='store_true')
    mode.add_argument('--batch', action='store_true')
    p.add_argument('--label', choices=LABELS)
    p.add_argument('--rps', type=float, default=8.0)
    p.add_argument('--start-slot', type=int, default=1)
    p.add_argument('--count', type=int, default=1)
    p.add_argument('--acknowledge-fresh-research-traffic', action='store_true')
    p.add_argument('--acknowledge-isolated-db-stop', action='store_true')
    a = p.parse_args(argv)
    label = 'HIGH_LOAD' if a.calibrate_high_load else a.label
    if label is None:
        p.error('--label required')
    if label == 'DATABASE_UNAVAILABLE' and a.rps == 8.0:
        a.rps = 1.5
    rate = validate_rate(label, a.rps)
    if a.start_slot < 1 or a.count < 1 or a.count > 10 or a.start_slot+a.count-1 > 12:
        raise ValueError('STOP: slots 1..12 and count <=10 only')
    if a.preview:
        print(f'READ ONLY PREVIEW: {label}, slots={a.start_slot}..{a.start_slot+a.count-1}, '
              f'{DURATION}s at {rate:g} RPS; exact 60s feature window')
        print('Isolated clone database only' if label == 'DATABASE_UNAVAILABLE' else
              'Measured latency/failure overload gate; high traffic alone is NOT sufficient')
        return 0
    if not a.acknowledge_fresh_research_traffic:
        raise ValueError('STOP: --acknowledge-fresh-research-traffic required')
    if label == 'DATABASE_UNAVAILABLE' and not a.acknowledge_isolated_db_stop:
        raise ValueError('STOP: isolated database stop requires explicit acknowledgment')
    if label != 'DATABASE_UNAVAILABLE' and a.acknowledge_isolated_db_stop:
        raise ValueError('STOP: database-stop acknowledgment is irrelevant')
    if a.calibrate_high_load:
        asyncio.run(calibrate_high_load(rate))
    else:
        for slot in range(a.start_slot, a.start_slot+a.count):
            print(f'BEGIN {label} SLOT {slot}', flush=True)
            asyncio.run(one_capture(label, slot, rate))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired,
            asyncio.TimeoutError, KeyError) as exc:
        print('FINAL TWO V2 CAPTURE STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
