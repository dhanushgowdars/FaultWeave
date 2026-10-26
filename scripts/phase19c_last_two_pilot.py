#!/usr/bin/env python3
"""Two bounded, diagnostic-only FaultWeave v2 research pilots.

DATABASE_UNAVAILABLE performs an *explicitly acknowledged* local PostgreSQL
container stop, always attempts restoration in finally, and never removes a
volume. HIGH_LOAD generates bounded elevated-rate traffic and only succeeds if
physical/observed overload evidence is present. No training admission.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2c_healthy_pilot import (
    json_bytes, jsonl_bytes, proven_client_rows, require_clean_local_stack,
    safe_url, visible_events,
)
from scripts.phase19c2d_fault_pilot import (
    correlation_gate, new_plan, parsed_time, recovery_gate,
)
from scripts.phase19c2j_timeout_pilot import blocked_plan_gate

SCHEMA = 'faultweave.v2.last-two-diagnostic.1'
OUT = ROOT / 'data/datasets/v2/pilot-fault'
REJECTED = ROOT / 'data/datasets/v2/rejected-last-two'
FAULTS = {'DATABASE_UNAVAILABLE': ('postgresql', 1.5), 'HIGH_LOAD': ('gateway', 8.0)}
CAPTURE_SECONDS = 75
LEASE_SECONDS = 145
POSTGRES_SERVICE = 'postgres'
BASELINE_RUN = 'v2pilot-ff5edf80e69444c4'


def validate_configuration(fault: str, target: str, rps: float) -> None:
    if fault not in FAULTS:
        raise ValueError('Only DATABASE_UNAVAILABLE and HIGH_LOAD are supported')
    expected_target, expected_rps = FAULTS[fault]
    if target != expected_target or not isinstance(rps, (int, float)) or isinstance(rps, bool) or not math.isfinite(rps) or float(rps) != expected_rps:
        raise ValueError(f'{fault}: only target={expected_target!r} and rps={expected_rps} are allowed')


def docker(*args: str, timeout: int = 120) -> str:
    done = subprocess.run(['docker', *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
    if done.returncode:
        raise RuntimeError(f'Docker operation failed ({args[:3]}): {done.stderr.strip()[:300]}')
    return done.stdout.strip()


def preflight_database_outage() -> str:
    """Local development-only. Abort before a stop if project/topology differ."""
    config = json.loads(docker('compose', 'config', '--format', 'json'))
    if config.get('name') != 'faultweave':
        raise RuntimeError('STOP: project is not the named local faultweave stack')
    svc = config.get('services', {}).get('postgres') or {}
    if str(svc.get('image', '')) != 'postgres:17-alpine':
        raise RuntimeError('STOP: unreviewed PostgreSQL image')
    volumes = svc.get('volumes') or []
    if not any(v.get('type') == 'volume' and v.get('target') == '/var/lib/postgresql/data' for v in volumes if isinstance(v, dict)):
        raise RuntimeError('STOP: expected named development PostgreSQL volume missing')
    container = docker('compose', 'ps', '-q', POSTGRES_SERVICE)
    if not container or len(container.splitlines()) != 1:
        raise RuntimeError('STOP: ambiguous PostgreSQL container')
    if docker('inspect', '--format', '{{.State.Health.Status}}', container) != 'healthy':
        raise RuntimeError('STOP: PostgreSQL not healthy before controlled outage')
    return container


def stop_local_postgres(container: str) -> dict:
    """Never remove volumes, containers or networks."""
    issued = datetime.now(UTC)
    docker('compose', 'stop', '--timeout', '10', POSTGRES_SERVICE, timeout=45)
    stopped = docker('inspect', '--format', '{{.State.Running}}', container).lower()
    if stopped != 'false':
        raise RuntimeError('STOP: PostgreSQL stop could not be verified')
    return {'container_id': container, 'docker_running_during_fault': False,
            'stop_verified_at': datetime.now(UTC).isoformat(),
            'stop_command_issued_at': issued.isoformat()}


def restore_local_postgres() -> None:
    # Always run restoration after any stop attempt, even on interruption/error.
    docker('compose', 'up', '-d', '--no-deps', '--wait', POSTGRES_SERVICE, timeout=180)


def load_healthy_reference() -> dict:
    """Use previously independently audited 1.5 RPS diagnostic; never label it."""
    folder = ROOT / 'data/datasets/v2/pilot' / BASELINE_RUN
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    artifact = manifest.get('artifacts', {}).get('candidate_feature.json', {})
    raw = (folder / 'candidate_feature.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != artifact.get('sha256') or len(raw) != artifact.get('bytes'):
        raise RuntimeError('STOP: healthy reference artifact digest mismatch')
    candidate = json.loads(raw)
    if candidate.get('run_id') != BASELINE_RUN or candidate.get('training_eligible') is not False:
        raise RuntimeError('STOP: invalid healthy reference')
    baseline = float(candidate['features']['request_latency_p95_ms'])
    if not math.isfinite(baseline) or baseline <= 0:
        raise RuntimeError('STOP: invalid healthy latency reference')
    return {'reference_run_id': BASELINE_RUN, 'reference_p95_ms': baseline,
            'reference_manifest_sha256': hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest()}


def in_window(window, requests: list[dict]) -> dict[str, dict]:
    selected = {r['request_id']: r for r in requests
                if window.started_at < parsed_time(r['started_at']) <= window.ended_at}
    if len(selected) < 60:
        raise RuntimeError(f'STOP: insufficient in-window requests ({len(selected)})')
    return selected


def effect_gate(window, requests: list[dict], events: list[dict], fault: str,
                *, stop_proof: dict | None, reference: dict | None) -> dict:
    selected = in_window(window, requests)
    failed = {rid for rid, r in selected.items()
              if r.get('transport_error') or not isinstance(r.get('status_code'), int)
              or r['status_code'] >= 500}
    corroborated = {e['request_id'] for e in events
                    if e.get('request_id') in failed and
                    e.get('trace_id') == selected[e['request_id']]['trace_id'] and
                    e.get('service') in {'gateway', 'transaction', 'payment', 'authentication'} and
                    window.started_at < parsed_time(e['timestamp']) <= window.ended_at and
                    (e.get('success') is False or
                     isinstance(e.get('status_code'), int) and e['status_code'] >= 500 or
                     e.get('event_type') in {'http_request_failed', 'downstream_request_failed', 'transaction_flow_failed'})}
    if fault == 'DATABASE_UNAVAILABLE':
        if not stop_proof or stop_proof.get('docker_running_during_fault') is not False:
            raise RuntimeError('STOP: no physical PostgreSQL-stop proof')
        if len(failed) < max(10, int(len(selected) * 0.50)) or len(corroborated) < 10:
            raise RuntimeError(f'STOP: database outage not observable: failed={len(failed)}, correlated={len(corroborated)}')
        return {'in_window_requests': len(selected), 'failed_clients': len(failed),
                'correlated_failure_clients': len(corroborated),
                'physical_postgres_stopped': True}
    if not reference:
        raise RuntimeError('STOP: no verified healthy-rate reference')
    gateway = {e['request_id'] for e in events
               if e.get('service') == 'gateway' and e.get('event_type') == 'http_request_completed'
               and e.get('request_id') in selected
               and e.get('trace_id') == selected[e['request_id']]['trace_id']
               and window.started_at < parsed_time(e['timestamp']) <= window.ended_at}
    latencies = sorted(float(r['latency_ms']) for r in selected.values()
                       if isinstance(r.get('latency_ms'), (int, float)))
    if len(latencies) != len(selected):
        raise RuntimeError('STOP: malformed high-load latency observations')
    p95 = latencies[min(len(latencies) - 1, int(.95 * len(latencies)))]
    rate = len(selected) / 60.0
    if len(selected) < 360 or len(gateway) < 300:
        raise RuntimeError(f'STOP: high-load volume/correlation insufficient: requests={len(selected)} gateway={len(gateway)}')
    # Label an *observable overload*, never traffic volume alone.
    evidenced = len(corroborated) >= 10 or (p95 >= 700 and p95 >= 1.8 * reference['reference_p95_ms'])
    if not evidenced:
        raise RuntimeError(f'STOP: ELEVATED_TRAFFIC_ONLY_NOT_PROVEN_OVERLOAD: rate={rate:.2f} p95={p95:.2f} '
                           f'baseline_p95={reference["reference_p95_ms"]:.2f} failed={len(failed)} correlated={len(corroborated)}')
    return {'in_window_requests': len(selected), 'observed_rps': round(rate, 3),
            'gateway_correlated_requests': len(gateway), 'client_p95_ms': round(p95, 3),
            'healthy_reference_p95_ms': reference['reference_p95_ms'],
            'failed_clients': len(failed), 'correlated_failure_clients': len(corroborated),
            'observed_overload': True}


def save_artifacts(run_id, requests, recovery, events, window, features, truth, metadata) -> Path:
    from datasets.final_features import FEATURE_NAMES
    if len(features) != 65 or tuple(features) != FEATURE_NAMES:
        raise RuntimeError('STOP: unexpected 65-feature schema/order')
    candidate = {'schema_version': SCHEMA, 'run_id': run_id, 'split': 'diagnostic_only',
                 'training_eligible': False, 'threshold_tuning_eligible': False, 'label': None,
                 'candidate_status': 'UNLABELED_LAST_TWO_DIAGNOSTIC',
                 'feature_count': 65, 'window_seconds': 60,
                 'window_started_at': window.started_at.isoformat(),
                 'window_ended_at': window.ended_at.isoformat(), 'features': features}
    bodies = {'requests.jsonl': jsonl_bytes(requests),
              'recovery_requests.jsonl': jsonl_bytes(recovery),
              'events.jsonl': jsonl_bytes(events),
              'candidate_feature.json': json_bytes(candidate),
              'protected_ground_truth.json': json_bytes(truth)}
    manifest = {'schema_version': SCHEMA, 'run_id': run_id,
                'intent': 'two remaining known-family diagnostic observations ONLY',
                'split': 'diagnostic_only', 'training_eligible': False,
                'threshold_tuning_eligible': False,
                'outgoing_header': 'X-Run-ID',
                'client_provenance': 'experiments.runner.execute_plan',
                'feature_schema_sha256': hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest(),
                'window_started_at': window.started_at.isoformat(),
                'window_ended_at': window.ended_at.isoformat(),
                'metadata': metadata,
                'artifacts': {k: {'sha256': hashlib.sha256(v).hexdigest(), 'bytes': len(v)}
                              for k, v in bodies.items()}}
    bodies['manifest.json'] = json_bytes(manifest)
    folder = OUT / run_id
    if folder.exists() or folder.is_symlink():
        raise RuntimeError('STOP: output directory already exists')
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir(exist_ok=False)
    for name, body in bodies.items():
        with (folder / name).open('xb') as stream:
            stream.write(body)
    return folder


def preserve_rejected(run_id: str, requests: list[dict], recovery: list[dict], events: list[dict], error: str) -> Path:
    path = REJECTED / run_id
    if path.exists() or path.is_symlink():
        raise RuntimeError('STOP: rejected-run directory already exists')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(exist_ok=False)
    files = {'requests.jsonl': jsonl_bytes(requests), 'recovery_requests.jsonl': jsonl_bytes(recovery),
             'events.jsonl': jsonl_bytes(events)}
    for name, body in files.items():
        with (path / name).open('xb') as fd:
            fd.write(body)
    with (path/'rejected.json').open('xb') as fd:
        fd.write(json_bytes({'run_id': run_id, 'reason': error, 'training_eligible': False,
                             'split': 'rejected_diagnostic', 'artifacts':
                             {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}))
    return path


async def run_pilot(fault: str, target: str, rps: float, gateway_url: str) -> Path:
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import execute_plan, collect_run_events

    validate_configuration(fault, target, rps)
    blocked_plan_gate()
    require_clean_local_stack()
    reference = load_healthy_reference() if fault == 'HIGH_LOAD' else None
    postgres_container = preflight_database_outage() if fault == 'DATABASE_UNAVAILABLE' else None
    run_id = 'v2finaltwo-' + uuid4().hex[:16]
    store = FaultStateStore(ROOT / 'data/fault-control')
    activation = store.acquire(run_id=run_id, fault_id=fault, target=target,
                               intensity='mild', duration_seconds=LEASE_SECONDS)
    stop_proof = None
    did_try_stop = False
    raw = None
    started = ended = None
    try:
        if fault == 'DATABASE_UNAVAILABLE':
            did_try_stop = True
            stop_proof = stop_local_postgres(postgres_container)
        started = datetime.now(UTC)
        # This is a bounded *75-second* workload; no model training or transfer endpoint.
        raw = await asyncio.wait_for(execute_plan(new_plan(run_id, rps), gateway_url,
                                                  run_id, 1931, 16), timeout=110)
        ended = datetime.now(UTC)
        if ended >= parsed_time(activation.expires_at):
            raise RuntimeError('STOP: active-lease deadline exceeded')
    finally:
        # Synchronous restoration ensures normal cancellation also reaches Docker.
        try:
            if did_try_stop:
                restore_local_postgres()
        finally:
            store.release(activation.activation_id)
        cleared = datetime.now(UTC)
    # Services may need time to reconnect after physical PostgreSQL restoration.
    for _ in range(35):
        try:
            require_clean_local_stack()
            break
        except RuntimeError:
            await asyncio.sleep(1)
    else:
        raise RuntimeError('STOP: not all seven services healthy after restoration')
    from experiments.runner import PlannedRequest
    recovery_plan = [PlannedRequest(sequence=10001 + i, offset_seconds=i / 1.5,
                                    scenario="valid", amount_minor=4000 + i) for i in range(8)]
    recovery_raw = await execute_plan(recovery_plan, gateway_url, run_id, 1932, 8)
    require_clean_local_stack()
    requests = proven_client_rows(raw, run_id)
    recovery = proven_client_rows(recovery_raw, run_id)
    events = visible_events(collect_run_events(run_id), run_id)
    try:
        correlation_gate(requests + recovery, events)
        recovery_proof = recovery_gate(recovery, events, cleared_at=cleared)
        watermark = max(parsed_time(e['timestamp']) for e in events)
        window = select_exact_window(
            phase_started_at=started, phase_ended_at=ended, telemetry_watermark=watermark,
            fault_activated_at=activation.activated_at,
            fault_expires_at=activation.expires_at, fault_cleared_at=cleared,
            fault_required=True, lead_guard_seconds=2, tail_guard_seconds=2,
        )
        vector = build_observable_features(run_id=run_id, window=window, requests=requests,
                                           events=events, require_boundary_evidence=True)
        impact = effect_gate(window, requests, events, fault, stop_proof=stop_proof, reference=reference)
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        path = preserve_rejected(run_id, requests, recovery, events, str(exc))
        print('REJECTED DIAGNOSTIC EVIDENCE:', path, file=sys.stderr)
        raise
    metadata = {'source_script': 'scripts/phase19c_last_two_pilot.py',
                'phase_started_at': started.isoformat(), 'phase_ended_at': ended.isoformat(),
                'telemetry_watermark': watermark.isoformat(), 'scheduled_rps': rps,
                'observed_requests': len(requests), 'observed_events': len(events),
                'impact': impact, 'recovery': recovery_proof,
                'healthy_reference': reference, 'passed_pre_post_stack_health': True}
    truth = {'schema_version': SCHEMA, 'run_id': run_id, 'fault_id': fault, 'target': target,
             'intensity': 'mild', 'activated_at': parsed_time(activation.activated_at).isoformat(),
             'expires_at': parsed_time(activation.expires_at).isoformat(),
             'cleared_at': cleared.isoformat(), 'physical_outage': stop_proof,
             'truth_role': 'audit-only evidence, prohibited as model features'}
    folder = save_artifacts(run_id, requests, recovery, events, window, vector, truth, metadata)
    print('LAST-TWO KNOWN-FAULT DIAGNOSTIC — NOT TRAINING DATA')
    print('RUN ID:', run_id, 'FAULT:', fault, 'TARGET:', target)
    print('OBSERVED EFFECT:', impact)
    print('RECOVERY:', recovery_proof)
    print('IMMUTABLE OUTPUT:', folder)
    return folder


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    m = parser.add_mutually_exclusive_group(required=True)
    m.add_argument('--dry-run', action='store_true')
    m.add_argument('--execute', action='store_true')
    parser.add_argument('--fault', choices=sorted(FAULTS), required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--rps', type=float, required=True)
    parser.add_argument('--gateway-url', default='http://127.0.0.1:18110')
    parser.add_argument('--acknowledge-diagnostic-only', action='store_true')
    parser.add_argument('--acknowledge-local-db-outage', action='store_true')
    args = parser.parse_args(argv)
    validate_configuration(args.fault, args.target, args.rps)
    safe_url(args.gateway_url)
    blocked_plan_gate()
    if args.dry_run:
        if args.acknowledge_diagnostic_only or args.acknowledge_local_db_outage:
            raise ValueError('Acknowledgements apply only to active execution')
        print(f'DRY RUN: {args.fault} target={args.target} rps={args.rps}, {CAPTURE_SECONDS}s, lease={LEASE_SECONDS}s')
        print('DATABASE_UNAVAILABLE STOPS AND RESTORES LOCAL POSTGRES ONLY when explicitly authorized')
        print('HIGH_LOAD is NOT called overload unless physical response degradation is proven')
        print('No Docker access, traffic, fault, files, model or dataset write')
        return 0
    if not args.acknowledge_diagnostic_only:
        raise ValueError('--acknowledge-diagnostic-only required')
    if args.fault == 'DATABASE_UNAVAILABLE' and not args.acknowledge_local_db_outage:
        raise ValueError('DB stop requires --acknowledge-local-db-outage')
    if args.fault == 'HIGH_LOAD' and args.acknowledge_local_db_outage:
        raise ValueError('Irrelevant DB-stop acknowledgement refused')
    asyncio.run(run_pilot(args.fault, args.target, args.rps, safe_url(args.gateway_url)))
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, ImportError, subprocess.TimeoutExpired, asyncio.TimeoutError) as exc:
        print('LAST-TWO PILOT STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
