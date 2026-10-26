"""FaultWeave V2: isolated, evaluation-only sealed-unknown capture.

NEVER invokes the Phase-6 final executor or the old sealed-unknown probe.
All traffic targets the fixed V2-isolated gateway. The fault lease is on the
isolated control volume only; source training samples and model are untouched.

Workflow, from repository root:
  python -m scripts.faultweave_v2_isolated_unknown_capture --preview
  python -m scripts.faultweave_v2_isolated_unknown_capture --preflight
  python -m scripts.faultweave_v2_isolated_unknown_capture --capture \
      --scenario INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE \
      --target payment->ledger --intensity medium \
      --acknowledge-isolated-sealed-unknown-traffic

Captures exactly one new run, no automatic retries. Do NOT evaluate/reveal
until separately validating the completed capture and frozen-model receipt.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = 'http://127.0.0.1:28110'
RPS = 1.5
FAULT_PHASE_SECONDS = 75
BASELINE_SECONDS = 15
LEASE_SECONDS = 145
RECOVERY_COUNT = 8
EVAL_ROOT = ROOT / 'data/datasets/final'
ATTEMPTS = ROOT / 'data/datasets/v2/evaluation-only/capture-attempts'
MANIFESTS = EVAL_ROOT / 'manifests'
RAW = EVAL_ROOT / 'v2-sealed-evaluation-only/raw'
TRUTH = EVAL_ROOT / 'v2-sealed-evaluation-only/ground_truth'
COLLECTOR = 'v2-isolated-sealed-unknown-eval.1'


def fail(message: str):
    raise RuntimeError('STOP: ' + message)


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_new(path: Path, obj) -> None:
    if path.exists() or path.is_symlink():
        fail(f'refuse to overwrite: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        fail(f'symlinked destination parent: {path.parent}')
    data = (json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as fd:
        fd.write(data)
        fd.flush()
        os.fsync(fd.fileno())


def jsonl_new(path: Path, items: list[dict]) -> None:
    if not items:
        fail('empty telemetry is not admissible')
    if path.exists() or path.is_symlink():
        fail(f'refuse to overwrite: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        fail('symlinked raw destination')
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as fd:
        for item in items:
            fd.write(json.dumps(item, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')
        fd.flush()
        os.fsync(fd.fileno())


def artifact(path: Path, n: int) -> dict:
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha_file(path), 'record_count': n}


def timestamp(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def validate_primary_stack() -> None:
    from scripts import faultweave_v2_isolated_db as iso
    for svc in iso.SERVICES:
        result = subprocess.run(['docker', 'compose', 'ps', '-q', svc], cwd=ROOT,
                                text=True, capture_output=True, check=False, timeout=30)
        ids = result.stdout.splitlines()
        if result.returncode or len(ids) != 1 or not re.fullmatch('[a-f0-9]{12,64}', ids[0]):
            fail(f'primary service missing or ambiguous: {svc}')
        check = subprocess.run(['docker', 'inspect', '--format', '{{.State.Health.Status}}', ids[0]],
                               text=True, capture_output=True, check=False, timeout=20)
        if check.returncode or check.stdout.strip() != 'healthy':
            fail(f'primary service not healthy: {svc}')


def preflight() -> None:
    from scripts import faultweave_v2_isolated_db as iso
    from scripts import faultweave_v2_candidate04_blind_eval as adapter
    adapter.verify_frozen()
    iso.inspect_healthy()
    validate_primary_stack()
    if iso.FIXTURE.is_symlink() or (iso.FIXTURE / 'fault-control').is_symlink():
        fail('isolated fault-control path must not be symlinked')
    if (iso.FIXTURE / 'fault-control/active_fault.json').exists():
        fail('isolated fault lease already active')
    if (ROOT / 'data/fault-control/active_fault.json').exists():
        fail('original fault lease active; no overlapping experiments')
    # Fresh evaluation artifacts only. No existing runs, training artifacts, or
    # model bundles are modified during preflight.
    print('PASS: frozen Candidate 04 + isolated and original seven-container stacks verified; leases idle')
    print('ISOLATED GATEWAY:', GATEWAY)
    print('FAULT PHASE:', FAULT_PHASE_SECONDS, 'seconds at', RPS, 'RPS')
    print('NO TRAFFIC SENT during preflight')


def planned(count: int, offset: int, rps: float):
    from experiments.runner import PlannedRequest
    return [PlannedRequest(sequence=offset+i, offset_seconds=i/rps,
                           scenario='valid', amount_minor=4000+(i % 200))
            for i in range(count)]


def stack_recovery_check() -> None:
    from scripts import faultweave_v2_isolated_db as iso
    iso.inspect_healthy()
    validate_primary_stack()


async def capture_once(scenario: str, target: str, intensity: str) -> None:
    from scripts import faultweave_v2_isolated_db as iso
    from scripts.faultweave_v2_final_two_capture import collect_correlation_checked_events
    from experiments.sealed_unknowns.catalog import validate_unknown_scenario
    from experiments.sealed_unknowns.lifecycle import SealedUnknownLease, SealedUnknownStateStore
    from experiments.runner import execute_plan
    from scripts.phase19c2c_healthy_pilot import proven_client_rows
    from scripts.phase19c2d_fault_pilot import parsed_time, correlation_gate, recovery_gate
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from datasets.final_features import FEATURE_NAMES
    from scripts.faultweave_v2_candidate04_blind_eval import only_numeric_features

    validate_unknown_scenario(scenario, target, intensity)
    preflight()
    run_id = 'v2blind-' + uuid4().hex[:16]
    attempt = ATTEMPTS / f'{run_id}.intent.json'
    manifest_path = MANIFESTS / f'{run_id}.json'
    raw_dir = RAW / run_id
    truth_path = TRUTH / f'{run_id}.json'
    if any(p.exists() or p.is_symlink() for p in (attempt, manifest_path, raw_dir, truth_path)):
        fail('unexpected output collision')
    # Durable create-only intent first; failures remain traceable, no auto retries.
    write_new(attempt, {'run_id': run_id, 'collector': COLLECTOR,
                        'scenario_type': 'sealed_unknown', 'split': 'evaluation_only',
                        'fault_id': scenario, 'target': target, 'intensity': intensity,
                        'isolation_project': iso.PROJECT, 'started_at': timestamp(datetime.now(UTC)),
                        'training_eligible': False, 'threshold_tuning_eligible': False})
    print('EVALUATION RUN:', run_id, flush=True)
    baseline = faults = recovery = []
    try:
        # Fixed isolated gateway, no user-overridable endpoint.
        count_baseline = round(BASELINE_SECONDS * RPS)
        baseline = proven_client_rows(await asyncio.wait_for(execute_plan(
            planned(count_baseline, 50001, RPS), GATEWAY, run_id, 2620, 24), timeout=50), run_id)
        if not all(row.get('expected_outcome') is True for row in baseline):
            fail('isolated baseline transactions unsuccessful')

        store = SealedUnknownStateStore(iso.FIXTURE / 'fault-control')
        activation = store.acquire(run_id=run_id, fault_id=scenario, target=target,
                                   intensity=intensity, duration_seconds=LEASE_SECONDS)
        phase_started = datetime.now(UTC)
        with SealedUnknownLease(store, activation, stack_recovery_check):
            faults = proven_client_rows(await asyncio.wait_for(execute_plan(
                planned(round(FAULT_PHASE_SECONDS * RPS), 1, RPS),
                GATEWAY, run_id, 2621, 24), timeout=115), run_id)
            phase_ended = datetime.now(UTC)
            if phase_ended >= parsed_time(activation.expires_at):
                fail('fault lease expired before fault traffic completed')
        fault_cleared = datetime.now(UTC)
        # The lease manager releases first, then checks both stacks.
        stack_recovery_check()

        recovery = proven_client_rows(await asyncio.wait_for(execute_plan(
            planned(RECOVERY_COUNT, 10001, RPS), GATEWAY, run_id, 2622, 8), timeout=40), run_id)
        if len(recovery) != RECOVERY_COUNT or any(row.get('expected_outcome') is not True for row in recovery):
            fail('recovery client requests did not all succeed')
        stack_recovery_check()

        all_clients = [*baseline, *faults, *recovery]
        if len({r['request_id'] for r in all_clients}) != len(all_clients):
            fail('duplicate request IDs')
        events = await collect_correlation_checked_events(run_id, True, all_clients, [])
        correlation_gate(all_clients, events)
        recovery_proof = recovery_gate(recovery, events, cleared_at=fault_cleared)
        if not events:
            fail('no structured service events')
        watermark = max(parsed_time(e['timestamp']) for e in events)
        window = select_exact_window(
            phase_started_at=phase_started, phase_ended_at=phase_ended,
            telemetry_watermark=watermark, fault_activated_at=activation.activated_at,
            fault_expires_at=activation.expires_at, fault_cleared_at=fault_cleared,
            fault_required=True, lead_guard_seconds=2, tail_guard_seconds=2)
        vector = build_observable_features(run_id=run_id, window=window,
                                           requests=faults, events=events,
                                           require_boundary_evidence=True)
        if len(FEATURE_NAMES) != 65 or tuple(vector) != FEATURE_NAMES:
            fail('frozen feature order/shape mismatch')
        # The offline adapter uses a [start,end) window. Enforce that its
        # future input exactly reproduces the collector's current 65 features.
        req_window = [r for r in faults if window.started_at <= parsed_time(r['started_at']) < window.ended_at]
        event_window = [e for e in events if window.started_at <= parsed_time(e['timestamp']) < window.ended_at]
        adapter_features = only_numeric_features(req_window, event_window)
        if any(float(vector[n]) != float(adapter_features[n]) for n in FEATURE_NAMES):
            fail('OFFLINE_FEATURE_CONTRACT_MISMATCH: adapter/collector windows disagree')
        if not req_window or not event_window:
            fail('empty evaluation feature window')
        # Observable impact proof is independent of hidden labels/model output.
        faulty = any(r.get('expected_outcome') is False for r in req_window)
        baseline_latency = sorted(float(r['latency_ms']) for r in baseline)
        p95_baseline = baseline_latency[min(len(baseline_latency)-1, int(.95*len(baseline_latency)))]
        phase_latency = sorted(float(r['latency_ms']) for r in req_window)
        p95_fault = phase_latency[min(len(phase_latency)-1, int(.95*len(phase_latency)))]
        service_fail = any(e.get('success') is False for e in event_window)
        impacted = faulty or service_fail or (p95_fault >= max(500.0, 1.8*p95_baseline))
        if not impacted:
            fail('NO_INDEPENDENTLY_OBSERVABLE_FAULT_EFFECT: do not score as a valid unknown experiment')

        # Persist raw evidence + protected ground truth BEFORE creating manifest.
        # All paths sit outside the 126-sample V2 training assembly.
        raw_dir.mkdir(parents=True, exist_ok=False)
        requests_path = raw_dir / 'requests.jsonl'
        events_path = raw_dir / 'events.jsonl'
        jsonl_new(requests_path, all_clients)
        jsonl_new(events_path, events)
        truth = {
            'schema_version': '1.0', 'run_id': run_id,
            'scenario_type': 'sealed_unknown', 'split': 'evaluation_only',
            'training_eligible': False, 'threshold_tuning_eligible': False,
            'fault_id': scenario, 'target': target, 'intensity': intensity,
            'isolation_project': iso.PROJECT, 'intervals': [
                {'name': 'fault', 'started_at': timestamp(window.started_at),
                 'ended_at': timestamp(window.ended_at)}],
            'fault_activation_started_at': timestamp(activation.activated_at),
            'fault_cleared_at': timestamp(fault_cleared),
            'recovery_checks': RECOVERY_COUNT, 'recovery_proof': recovery_proof,
            'feature_window_seconds': 60, 'model_input_label_free': True,
            'effect_observed': {'client_failure': faulty, 'service_failure': service_fail,
                                'baseline_p95_ms': round(p95_baseline, 3),
                                'fault_p95_ms': round(p95_fault, 3)},
        }
        write_new(truth_path, truth)
        manifest = {
            'schema_version': '1.0', 'dataset_id': 'faultweave-final-dataset-v1',
            'run_id': run_id, 'scenario_type': 'sealed_unknown', 'split': 'evaluation_only',
            'training_eligible': False, 'threshold_tuning_eligible': False,
            'recovery_verified': True, 'accepted': True,
            'isolation_project': iso.PROJECT, 'collector': COLLECTOR,
            'started_at': timestamp(phase_started), 'ended_at': timestamp(datetime.now(UTC)),
            'requests': artifact(requests_path, len(all_clients)),
            'events': artifact(events_path, len(events)),
            'ground_truth': artifact(truth_path, 1),
        }
        write_new(manifest_path, manifest)
        print('EVALUATION-ONLY MANIFEST CREATED:', manifest_path.relative_to(ROOT).as_posix())
        print('INDEPENDENT CHECK REQUIRED: do not reveal or update model before freezing prediction receipt')
    except BaseException:
        print('STOP: failed attempt preserved at', attempt, '; never retry same run automatically', file=sys.stderr)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true')
    mode.add_argument('--preflight', action='store_true')
    mode.add_argument('--capture', action='store_true')
    parser.add_argument('--scenario', choices=[
        'INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE',
        'LATENCY_JITTER_PARTIAL_DEGRADATION'])
    parser.add_argument('--target')
    parser.add_argument('--intensity', choices=['mild', 'medium', 'high'])
    parser.add_argument('--acknowledge-isolated-sealed-unknown-traffic', action='store_true')
    args = parser.parse_args(argv)
    if args.preview:
        if args.acknowledge_isolated_sealed_unknown_traffic:
            fail('preview must not have execution acknowledgement')
        print('READ ONLY PREVIEW: isolated gateway', GATEWAY)
        print('Fault phase 75s at 1.5 RPS; exact 60s feature window; separate 15s baseline and 8 recovery requests')
        print('Scenario selected:', args.scenario or 'not selected')
        print('Only evaluation-only artifact creation, no training data or model changes.')
        return 0
    if args.preflight:
        if args.scenario or args.target or args.intensity or args.acknowledge_isolated_sealed_unknown_traffic:
            fail('preflight accepts no capture-specific args')
        preflight()
        return 0
    if not (args.scenario and args.target and args.intensity and args.acknowledge_isolated_sealed_unknown_traffic):
        fail('capture requires scenario, target, intensity and explicit acknowledgement')
    asyncio.run(capture_once(args.scenario, args.target, args.intensity))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, KeyError, OSError, TimeoutError,
            subprocess.TimeoutExpired, asyncio.TimeoutError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(2)
