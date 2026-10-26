#!/usr/bin/env python3
"""Fail-closed single-payment resource-fault diagnostic. Never trains or executes plan rows.

CONNECTION_POOL_EXHAUSTION: one worker, 15-connection checkout, physical DB
connection pressure plus correlated client failures required.
DATABASE_LOCK_CONTENTION: advisory-lock waiters physically observed while client
requests fail; samples remain diagnostic evidence, excluded from 65 features.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2c_healthy_pilot import (json_bytes, jsonl_bytes, proven_client_rows,
                                              require_clean_local_stack, safe_url, visible_events)
from scripts.phase19c2d_fault_pilot import (DURATION_SECONDS, LEASE_SECONDS,
                                             correlation_gate, new_plan, parsed_time,
                                             recovery_gate)
from scripts.phase19c2j_timeout_pilot import blocked_plan_gate

SCHEMA = '19c2n.resource-diagnostic.1'
FAULTS = ('CONNECTION_POOL_EXHAUSTION', 'DATABASE_LOCK_CONTENTION')
TARGET = 'payment'
RATE = 1.5
INTENSITY = {'CONNECTION_POOL_EXHAUSTION': 'high',
             'DATABASE_LOCK_CONTENTION': 'medium'}
SOURCE_SCRIPT = 'scripts/phase19c2n_resource_pilot.py'
DESTINATION = ROOT / 'data/datasets/v2/pilot-fault'
SAMPLE_INTERVAL_SECONDS = 2.0
MIN_REMAINING_CONNECTIONS = 40
# Single payment process, high pool uses 15 pooled connections. Account for
# background health checks and one metrics session before allowing the test.
MAX_FAULT_CONNECTIONS = 15
SQL_SNAPSHOT = ("SELECT (SELECT count(*) FROM pg_stat_activity),"
                "(SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted),"
                "(SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted);")


def configuration(fault: str, target: str, rps: float) -> None:
    if fault not in FAULTS or target != TARGET or isinstance(rps, bool) or rps != RATE:
        raise ValueError('Only bounded payment resource diagnostics at exactly 1.5 RPS are supported')


def command(*args: str) -> str:
    result = subprocess.run(['docker', 'compose', *args], cwd=ROOT,
                            capture_output=True, text=True, timeout=20, check=False)
    if result.returncode:
        raise RuntimeError('Docker/psql preflight failed: ' + (result.stderr.strip() or result.stdout.strip())[:350])
    return result.stdout.strip()


def database_snapshot(username: str | None = None, database: str | None = None) -> dict:
    """Read only, no SQL parameter ever assembled from user input."""
    user = username or command('exec', '-T', 'postgres', 'printenv', 'POSTGRES_USER')
    database = database or command('exec', '-T', 'postgres', 'printenv', 'POSTGRES_DB')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', user) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', database):
        raise RuntimeError('Unsafe PostgreSQL database/user environment')
    snapshot = command('exec', '-T', 'postgres', 'psql', '-U', user, '-d', database,
                       '-At', '-F', ',', '-c', SQL_SNAPSHOT)
    try:
        values = [int(x) for x in snapshot.splitlines()[-1].split(',')]
    except (ValueError, IndexError) as exc:
        raise RuntimeError('Malformed PostgreSQL snapshot') from exc
    if len(values) != 3 or any(v < 0 for v in values):
        raise RuntimeError('Unexpected PostgreSQL snapshot dimensions')
    return dict(zip(('total_connections', 'advisory_waiters', 'advisory_holders'), values))


def verify_running_payment_worker() -> None:
    """Inspect the actual container, not only the editable Compose settings."""
    ids = command('ps', '-q', 'payment-service').splitlines()
    if len(ids) != 1 or not re.fullmatch(r'[a-fA-F0-9]{12,64}', ids[0]):
        raise RuntimeError('STOP: payment container identity ambiguous')
    def inspect(field: str):
        result = subprocess.run(['docker', 'inspect', '--format',
                                 '{{json .Config.' + field + '}}', ids[0]],
                                capture_output=True, text=True, check=False, timeout=20)
        if result.returncode:
            raise RuntimeError('STOP: cannot verify running payment container configuration')
        return json.loads(result.stdout.strip())
    cmd = inspect('Cmd')
    environ = inspect('Env')
    if not isinstance(cmd, list) or not isinstance(environ, list):
        raise RuntimeError('STOP: running payment container metadata invalid')
    if any(flag in str(arg) for arg in cmd for flag in ('--workers', '--reload')):
        raise RuntimeError('STOP: running payment container has an unapproved worker count')
    for setting in environ:
        if isinstance(setting, str) and setting.startswith('WEB_CONCURRENCY='):
            if setting != 'WEB_CONCURRENCY=1':
                raise RuntimeError('STOP: payment WEB_CONCURRENCY is not single-worker')


def ensure_live_preflight(fault: str) -> dict:
    """Fail closed on a changed worker config, old image or inadequate headroom."""
    blocked_plan_gate()
    require_clean_local_stack()
    if (ROOT / 'data/fault-control/active_fault.json').exists():
        raise RuntimeError('STOP: another global fault lease is present')
    expected = hashlib.sha256((ROOT / 'shared/faultweave_common/fault_injection.py').read_bytes()).hexdigest()
    live = command('exec', '-T', 'payment-service', 'python', '-c',
                   'import pathlib,hashlib,faultweave_common.fault_injection as f; '
                   'print(hashlib.sha256(pathlib.Path(f.__file__).read_bytes()).hexdigest())')
    if live != expected:
        raise RuntimeError('STOP: payment service does not run the current local fault framework')
    config = json.loads(command('config', '--format', 'json'))
    service = config.get('services', {}).get('payment-service', {})
    if (not service or service.get('scale', 1) != 1 or
        (service.get('deploy') or {}).get('replicas', 1) != 1):
        raise RuntimeError('STOP: payment service deployment topology not approved')
    cmd = service.get('command') or []
    cmd = cmd.split() if isinstance(cmd, str) else cmd
    if any(str(x).startswith('--workers') for x in cmd):
        raise RuntimeError('STOP: payment workers configured explicitly; re-review resource budget')
    verify_running_payment_worker()
    pguser = command('exec', '-T', 'postgres', 'printenv', 'POSTGRES_USER')
    pgdb = command('exec', '-T', 'postgres', 'printenv', 'POSTGRES_DB')
    max_value = int(command('exec', '-T', 'postgres', 'psql', '-U', pguser, '-d', pgdb,
                            '-At', '-c', 'SHOW max_connections;'))
    super_reserved = int(command('exec', '-T', 'postgres', 'psql', '-U', pguser, '-d', pgdb,
                                 '-At', '-c', 'SHOW superuser_reserved_connections;'))
    reserved = int(command('exec', '-T', 'postgres', 'psql', '-U', pguser, '-d', pgdb,
                           '-At', '-c', 'SHOW reserved_connections;'))
    snapshot = database_snapshot()
    safe_after = max_value - snapshot['total_connections'] - super_reserved - reserved - MAX_FAULT_CONNECTIONS
    if max_value < 60 or safe_after < MIN_REMAINING_CONNECTIONS:
        raise RuntimeError('STOP: insufficient connection budget after reserved slots and possible fault consumption')
    return {'max_connections': max_value, 'baseline_connections': snapshot['total_connections'],
            'reserved': super_reserved + reserved, 'remaining_after_allowance': safe_after,
            'baseline_waiters': snapshot['advisory_waiters'], 'fault': fault}


async def monitor_samples(stop: asyncio.Event, items: list[dict], username: str, database: str) -> None:
    """Physical snapshots taken throughout the fault. Monitoring failure stops pilot."""
    while not stop.is_set():
        record = await asyncio.to_thread(database_snapshot, username, database)
        items.append({'timestamp': datetime.now(UTC).isoformat(), **record})
        try:
            await asyncio.wait_for(stop.wait(), timeout=SAMPLE_INTERVAL_SECONDS)
        except TimeoutError:
            pass


def is_observable_service_failure(event: dict) -> bool:
    """Use the saved event schema; never infer failures from log level alone.

    `success` is a boolean derived by the structured log formatter. Pair it
    with HTTP 5xx or a narrow, explicit server-failure event type. Do not use
    unsaved `outcome`, arbitrary warning levels, or `error_type` strings.
    """
    if event.get('success') is not False:
        return False
    status = event.get('status_code')
    event_type = event.get('event_type')
    if event_type == 'http_request_completed':
        return type(status) is int and 500 <= status <= 599
    if event_type in ('downstream_request_failed', 'http_request_failed',
                      'transaction_flow_failed'):
        return status is None or (type(status) is int and 500 <= status <= 599)
    return False


def effect_gate(window, requests: list[dict], events: list[dict], samples: list[dict],
                fault: str, baseline: dict) -> dict:
    configuration(fault, TARGET, RATE)
    in_window = {r['request_id']: r for r in requests
                 if window.started_at < parsed_time(r['started_at']) <= window.ended_at}
    if len(in_window) < 60:
        raise RuntimeError('STOP: insufficient client coverage')
    failed = {rid for rid, r in in_window.items()
              if r.get('transport_error') is not None or
              not isinstance(r.get('status_code'), int) or r['status_code'] >= 500}
    # visible_events() exports the allowlisted `success`, `status_code` and
    # `event_type` fields, NEVER the raw logger `outcome` field. Require an
    # explicit unsuccessful service event with concrete server-error evidence;
    # mere warning levels or client failures are not sufficient.
    correlated = {e.get('request_id') for e in events
                  if e.get('request_id') in failed and e.get('trace_id') == in_window[e['request_id']]['trace_id']
                  and e.get('service') in ('gateway', 'transaction', 'payment')
                  and is_observable_service_failure(e)
                  and window.started_at < parsed_time(e['timestamp']) <= window.ended_at}
    during = [s for s in samples if window.started_at < parsed_time(s['timestamp']) <= window.ended_at]
    if len(during) < 10 or len(correlated) < 10:
        raise RuntimeError(
            'STOP: insufficient physical samples or correlated client failures '
            f'(physical_samples={len(during)}/10, correlated_client_failures={len(correlated)}/10, '
            f'in_window_requests={len(in_window)}, failed_clients={len(failed)}, '
            f'total_physical_samples={len(samples)}, total_events={len(events)})'
        )
    connection_peak = max(s['total_connections'] for s in during)
    waiters_peak = max(s['advisory_waiters'] for s in during)
    if fault == 'CONNECTION_POOL_EXHAUSTION':
        if connection_peak - baseline['baseline_connections'] < 8:
            raise RuntimeError('STOP: no independently observed database connection pressure')
    else:
        if waiters_peak <= baseline['baseline_waiters']:
            raise RuntimeError('STOP: no independently observed PostgreSQL advisory lock waiters')
    return {'fault_id': fault, 'target': TARGET, 'in_window_requests': len(in_window),
            'correlated_client_failures': len(correlated), 'physical_samples': len(during),
            'baseline_connections': baseline['baseline_connections'],
            'peak_connections': connection_peak, 'baseline_waiters': baseline['baseline_waiters'],
            'peak_waiters': waiters_peak}


def persist(run_id, requests, recovery, events, samples, window, features, truth, metadata) -> Path:
    from datasets.final_features import FEATURE_NAMES
    if len(FEATURE_NAMES) != 65 or tuple(features) != FEATURE_NAMES:
        raise RuntimeError('STOP: 65-feature schema mismatch')
    candidate = {'schema_version': SCHEMA, 'run_id': run_id,
                 'split': 'diagnostic_only', 'training_eligible': False,
                 'threshold_tuning_eligible': False, 'label': None,
                 'candidate_status': 'UNLABELED_RESOURCE_DIAGNOSTIC',
                 'feature_count': 65, 'window_seconds': 60,
                 'window_started_at': window.started_at.isoformat(),
                 'window_ended_at': window.ended_at.isoformat(), 'features': features}
    files = {'requests.jsonl': jsonl_bytes(requests),
             'recovery_requests.jsonl': jsonl_bytes(recovery),
             'events.jsonl': jsonl_bytes(events),
             'database_samples.jsonl': jsonl_bytes(samples),
             'candidate_feature.json': json_bytes(candidate),
             'protected_ground_truth.json': json_bytes(truth)}
    manifest = {'schema_version': SCHEMA, 'run_id': run_id,
                'intent': 'physical resource diagnostic, never training data',
                'split': 'diagnostic_only', 'training_eligible': False,
                'threshold_tuning_eligible': False,
                'feature_schema_sha256': hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest(),
                'window_started_at': window.started_at.isoformat(),
                'window_ended_at': window.ended_at.isoformat(), 'metadata': metadata,
                'artifacts': {key: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
                              for key, raw in files.items()}}
    files['manifest.json'] = json_bytes(manifest)
    folder = DESTINATION / run_id
    if folder.exists() or folder.is_symlink():
        raise RuntimeError('STOP: immutable destination exists')
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir(exist_ok=False)
    for name, raw in files.items():
        with (folder / name).open('xb') as fd:
            fd.write(raw)
    return folder



def persist_rejected_gate_evidence(run_id: str, requests: list[dict],
                                   recovery: list[dict], events: list[dict],
                                   samples: list[dict], window, reason: str) -> Path:
    """Preserve *rejected* gate evidence. Never write a model candidate or a label.

    Strictly separate from diagnostic-accepted and future training roots; immutable
    creation, checksums, and no modifications to existing artifacts or journals.
    """
    if not re.fullmatch(r'v2resource-[0-9a-f]{16}', run_id):
        raise ValueError('STOP: malformed rejected evidence run ID')
    folder = ROOT / 'data/datasets/v2/rejected-resource' / run_id
    files = {
        'requests.jsonl': jsonl_bytes(requests),
        'recovery_requests.jsonl': jsonl_bytes(recovery),
        'events.jsonl': jsonl_bytes(events),
        'database_samples.jsonl': jsonl_bytes(samples),
    }
    manifest = {
        'schema_version': '19c2n.rejected-resource-evidence.1',
        'run_id': run_id,
        'verdict': 'REJECTED_RESOURCE_EFFECT_GATE',
        'reason': reason,
        'split': 'diagnostic_only',
        'label': None,
        'training_eligible': False,
        'threshold_tuning_eligible': False,
        'window_started_at': window.started_at.isoformat(),
        'window_ended_at': window.ended_at.isoformat(),
        'feature_vector_stored': False,
        'artifacts': {name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                      for name, raw in files.items()},
    }
    files['manifest.json'] = json_bytes(manifest)
    if folder.exists() or folder.is_symlink():
        raise RuntimeError('STOP: rejected evidence destination already exists')
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir(exist_ok=False)
    for name, raw in files.items():
        with (folder / name).open('xb') as stream:
            stream.write(raw)
    return folder


async def run_pilot(fault: str, gateway_url: str) -> Path:
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import execute_plan, collect_run_events

    configuration(fault, TARGET, RATE)
    preflight = ensure_live_preflight(fault)
    run_id = 'v2resource-' + uuid4().hex[:16]
    store = FaultStateStore(ROOT / 'data/fault-control')
    db_user = command('exec', '-T', 'postgres', 'printenv', 'POSTGRES_USER')
    db_name = command('exec', '-T', 'postgres', 'printenv', 'POSTGRES_DB')
    activation = store.acquire(run_id=run_id, fault_id=fault, target=TARGET,
                               intensity=INTENSITY[fault], duration_seconds=LEASE_SECONDS)
    records = []
    stop = asyncio.Event()
    collector = asyncio.create_task(monitor_samples(stop, records, db_user, db_name))
    try:
        started = datetime.now(UTC)
        raw = await execute_plan(new_plan(run_id, RATE), gateway_url, run_id, 1913, 16)
        ended = datetime.now(UTC)
        if ended >= parsed_time(activation.expires_at):
            raise RuntimeError('STOP: lease expired during collection')
    finally:
        store.release(activation.activation_id)
        cleared = datetime.now(UTC)
        stop.set()
        await collector
    # Poll health rather than assuming instant recovery. Never bypass this gate.
    last_health_error = None
    for _ in range(35):
        await asyncio.sleep(1)
        try:
            await asyncio.to_thread(require_clean_local_stack)
        except RuntimeError as exc:
            last_health_error = exc
        else:
            break
    else:
        raise RuntimeError('STOP: stack failed to recover health after lease clear') from last_health_error
    recovered_raw = await execute_plan(new_plan(run_id, RATE, recovery=True),
                                       gateway_url, run_id, 1914, 8)
    require_clean_local_stack()
    requests = proven_client_rows(raw, run_id)
    recovery = proven_client_rows(recovered_raw, run_id)
    events = visible_events(collect_run_events(run_id), run_id)
    correlation_gate(requests + recovery, events)
    recovery_proof = recovery_gate(recovery, events, cleared_at=cleared)
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
    try:
        effect = effect_gate(window, requests, events, records, fault, preflight)
    except RuntimeError as failure:
        # All resource leases have already been cleared and recovery verified.
        # The original safety gate remains strictly unchanged; only rejected
        # observational evidence is preserved outside accepted/training roots.
        try:
            rejected_path = persist_rejected_gate_evidence(
                run_id, requests, recovery, events, records, window, str(failure)
            )
        except (OSError, RuntimeError, ValueError) as save_error:
            raise RuntimeError(
                f'{failure}; failed to persist rejected evidence: {save_error}'
            ) from failure
        raise RuntimeError(
            f'{failure}; REJECTED_DIAGNOSTIC_EVIDENCE: {rejected_path}'
        ) from failure
    metadata = {'source_script': SOURCE_SCRIPT, 'scheduled_rps': RATE,
                'phase_started_at': started.isoformat(), 'phase_ended_at': ended.isoformat(),
                'telemetry_watermark': watermark.isoformat(),
                'observed_requests': len(requests), 'observed_events': len(events),
                'preflight': preflight, 'impact': effect, 'recovery': recovery_proof,
                'passed_pre_post_stack_health': True, 'training_eligibility': False}
    truth = {'schema_version': SCHEMA, 'run_id': run_id,
             'fault_id': fault, 'target': TARGET, 'intensity': INTENSITY[fault],
             'activated_at': parsed_time(activation.activated_at).isoformat(),
             'expires_at': parsed_time(activation.expires_at).isoformat(),
             'cleared_at': cleared.isoformat(),
             'truth_role': 'protected validation only; excluded from model features'}
    saved = persist(run_id, requests, recovery, events, records, window,
                    features, truth, metadata)
    print('RESOURCE DIAGNOSTIC CANDIDATE — NOT TRAINING DATA')
    print('RUN ID:', run_id, 'FAULT:', fault, 'TARGET:', TARGET)
    print('DATABASE PRESSURE:', effect)
    print('RECOVERY:', recovery_proof)
    print('EXCLUSIVE OUTPUT:', saved)
    return saved


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    p.add_argument('--fault', choices=FAULTS, required=True)
    p.add_argument('--target', default=TARGET)
    p.add_argument('--rps', type=float, default=RATE)
    p.add_argument('--gateway-url', default='http://127.0.0.1:18110')
    p.add_argument('--acknowledge-diagnostic-only', action='store_true')
    args = p.parse_args(argv)
    configuration(args.fault, args.target, args.rps)
    safe_url(args.gateway_url)
    blocked_plan_gate()
    if args.dry_run:
        if args.acknowledge_diagnostic_only:
            raise ValueError('Acknowledgement is forbidden in dry-run mode')
        print('DRY RUN ONLY:', args.fault, TARGET, RATE, 'RPS', INTENSITY[args.fault],
              'fault lease', LEASE_SECONDS)
        print('NO Docker/network/fault or file operations; all 240 plan rows blocked')
        return 0
    if not args.acknowledge_diagnostic_only:
        raise ValueError('Live experiment requires explicit diagnostic-only acknowledgement')
    asyncio.run(run_pilot(args.fault, safe_url(args.gateway_url)))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, ImportError, TypeError, KeyError) as exc:
        print('RESOURCE PILOT STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
