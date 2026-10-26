#!/usr/bin/env python3
"""Bounded Model v2 service-effect diagnostic for two pre-existing known mechanisms.

This does NOT execute or approve any of the 240 planned training runs. It invokes
run-scoped middleware/database latency already in FaultWeave, never stops Docker,
never changes model weights, and writes only unlabelled diagnostic artifacts.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2c_healthy_pilot import (  # noqa: E402
    json_bytes, jsonl_bytes, proven_client_rows, require_clean_local_stack,
    safe_url, visible_events,
)
from scripts.phase19c2d_fault_pilot import (  # noqa: E402
    DURATION_SECONDS, LEASE_SECONDS, checked_rps, correlation_gate,
    new_plan, parsed_time, recovery_gate,
)
from scripts.phase19c2j_timeout_pilot import blocked_plan_gate  # noqa: E402

SCHEMA = '19c2m.service-effect.1'
ALLOWED = {
    'DATABASE_HIGH_LATENCY': {'payment', 'transaction', 'ledger'},
    'SERVICE_UNAVAILABLE': {'payment', 'ledger'},
}
RESERVED_BUT_NOT_SAFE = frozenset({
    'CONNECTION_POOL_EXHAUSTION', 'DATABASE_LOCK_CONTENTION',
    'DATABASE_UNAVAILABLE', 'HIGH_LOAD',
})
INTENSITY = 'medium'
RATE = 1.5
OUT_DIR = ROOT / 'data/datasets/v2/pilot-fault'
SOURCE_SCRIPT = 'scripts/phase19c2m_service_effect_pilot.py'
TARGET_PATHS = {
    'payment': '/internal/v1/payments',
    'transaction': '/internal/v1/transactions',
    'ledger': '/internal/v1/ledger',
}


def allowed_configuration(fault: str, target: str, rps: float) -> None:
    if fault not in ALLOWED or target not in ALLOWED[fault]:
        raise ValueError('This diagnostic supports only safe payment/transaction/ledger latency '
                         'or payment/ledger middleware-503 targets; infrastructure/pool/lock/load blocked')
    if checked_rps(rps) != RATE:
        raise ValueError('Only 1.5 RPS is approved for this bounded diagnostic')


def _in_window(window, requests: list[dict]) -> dict[str, dict]:
    selected = {r['request_id']: r for r in requests
                if window.started_at < parsed_time(r['started_at']) <= window.ended_at}
    if len(selected) < 60:
        raise RuntimeError('INSUFFICIENT_IN_WINDOW_REQUESTS')
    return selected


def observed_impact(window, requests: list[dict], events: list[dict], fault: str, target: str) -> dict:
    """Strict independent provenance and measured target evidence, not injected fault labels."""
    allowed_configuration(fault, target, RATE)
    in_window = _in_window(window, requests)
    prefix = TARGET_PATHS[target]
    target_events = [e for e in events
                     if e.get('event_type') == 'http_request_completed'
                     and e.get('service') == target
                     and str(e.get('path') or '').startswith(prefix)
                     and window.started_at < parsed_time(e['timestamp']) <= window.ended_at
                     and e.get('request_id') in in_window
                     and e.get('trace_id') == in_window[e['request_id']]['trace_id']]
    # Distinct *clients* must exhibit an effect, not just multiple events of one client.
    if fault == 'DATABASE_HIGH_LATENCY':
        matched = {e['request_id'] for e in target_events
                   if isinstance(e.get('latency_ms'), (int, float))
                   and not isinstance(e.get('latency_ms'), bool)
                   and e['latency_ms'] >= 600 and e.get('status_code') is not None}
        slow_clients = {rid for rid, r in in_window.items()
                        if isinstance(r.get('latency_ms'), (int, float))
                        and r['latency_ms'] >= 600}
        joint = matched & slow_clients
        if len(joint) < 10 or len(joint) < len(in_window) * .50:
            raise RuntimeError('DATABASE_DELAY_NOT_PROVEN: need >=50% distinct clients '
                               'with >=600ms correlated target and client latency')
        return {'fault_id': fault, 'target': target, 'in_window_requests': len(in_window),
                'correlated_slow_target_clients': len(matched),
                'correlated_slow_client_and_target': len(joint),
                'latency_threshold_ms': 600}
    affected = {rid for rid, r in in_window.items()
                if r.get('transport_error') is not None or r.get('status_code') is None
                or isinstance(r.get('status_code'), int) and r['status_code'] >= 500}
    rejected = {e['request_id'] for e in target_events if e.get('status_code') == 503}
    linked = rejected & affected
    if len(linked) < 10 or len(linked) < len(in_window) * .50:
        raise RuntimeError('SERVICE_503_NOT_PROVEN: need >=50% clients with correlated '
                           'target-service HTTP 503 and propagated error')
    return {'fault_id': fault, 'target': target, 'in_window_requests': len(in_window),
            'affected_client_requests': len(affected),
            'correlated_target_503_clients': len(rejected),
            'correlated_503_with_client_impact': len(linked)}


async def execute_fault_phase(store, run_id: str, fault: str, target: str,
                              plan: list, gateway_url: str, executor):
    """Lease is released under exceptions/cancellation; no Docker stop or DB mutation."""
    allowed_configuration(fault, target, RATE)
    activation = store.acquire(run_id=run_id, fault_id=fault, target=target,
                               intensity=INTENSITY, duration_seconds=LEASE_SECONDS)
    try:
        active = store.active()
        if active is None or active.activation_id != activation.activation_id:
            raise RuntimeError('Fault lease ownership mismatch')
        started = datetime.now(UTC)
        raw = await executor(plan, gateway_url, run_id, 1913, 16)
        ended = datetime.now(UTC)
        if ended >= parsed_time(activation.expires_at):
            raise RuntimeError('Fault lease expired during workload')
    finally:
        store.release(activation.activation_id)
        cleared = datetime.now(UTC)
    return raw, started, ended, activation, cleared


def persist(run_id: str, requests: list[dict], recovery: list[dict], events: list[dict],
            window, features: dict, truth: dict, metadata: dict) -> Path:
    from datasets.final_features import FEATURE_NAMES
    if len(FEATURE_NAMES) != 65 or tuple(features) != FEATURE_NAMES:
        raise RuntimeError('Unapproved feature schema/order')
    candidate = {'schema_version': SCHEMA, 'run_id': run_id,
                 'split': 'diagnostic_only', 'training_eligible': False,
                 'threshold_tuning_eligible': False, 'label': None,
                 'candidate_status': 'UNLABELED_SERVICE_EFFECT_DIAGNOSTIC',
                 'feature_count': 65, 'window_seconds': 60,
                 'window_started_at': window.started_at.isoformat(),
                 'window_ended_at': window.ended_at.isoformat(), 'features': features}
    contents = {'requests.jsonl': jsonl_bytes(requests),
                'recovery_requests.jsonl': jsonl_bytes(recovery),
                'events.jsonl': jsonl_bytes(events),
                'candidate_feature.json': json_bytes(candidate),
                'protected_ground_truth.json': json_bytes(truth)}
    manifest = {'schema_version': SCHEMA, 'run_id': run_id,
                'intent': 'bounded existing known fault service diagnostic, no training admission',
                'split': 'diagnostic_only', 'training_eligible': False,
                'threshold_tuning_eligible': False, 'outgoing_header': 'X-Run-ID',
                'client_provenance': 'experiments.runner.execute_plan',
                'feature_schema_sha256': hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest(),
                'window_started_at': window.started_at.isoformat(),
                'window_ended_at': window.ended_at.isoformat(),
                'metadata': metadata,
                'artifacts': {n: {'sha256': hashlib.sha256(b).hexdigest(), 'bytes': len(b)}
                              for n, b in contents.items()}}
    contents['manifest.json'] = json_bytes(manifest)
    destination = OUT_DIR / run_id
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('STOP: immutable output already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    # Keep partial evidence on write error. Never reuse or overwrite a run ID.
    for name, content in contents.items():
        with (destination/name).open('xb') as fd:
            fd.write(content)
    return destination


async def run_pilot(fault: str, target: str, gateway_url: str) -> Path:
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import collect_run_events, execute_plan

    blocked_plan_gate()
    require_clean_local_stack()
    run_id = 'v2effect-' + uuid4().hex[:16]
    store = FaultStateStore(ROOT / 'data/fault-control')
    raw, started, ended, activation, cleared = await execute_fault_phase(
        store, run_id, fault, target, new_plan(run_id, RATE), gateway_url, execute_plan)
    require_clean_local_stack()
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
                                 fault_cleared_at=cleared,
                                 fault_required=True, lead_guard_seconds=2, tail_guard_seconds=2)
    features = build_observable_features(run_id=run_id, window=window,
                                         requests=requests, events=events,
                                         require_boundary_evidence=True)
    effect = observed_impact(window, requests, events, fault, target)
    metadata = {'phase_started_at': started.isoformat(), 'phase_ended_at': ended.isoformat(),
                'telemetry_watermark': watermark.isoformat(), 'scheduled_rps': RATE,
                'observed_requests': len(requests), 'observed_events': len(events),
                'recovery': recovery_proof, 'impact': effect,
                'passed_pre_post_stack_health': True, 'source_script': SOURCE_SCRIPT,
                'training_eligibility': False}
    truth = {'schema_version': SCHEMA, 'run_id': run_id, 'fault_id': fault, 'target': target,
             'intensity': INTENSITY, 'activated_at': parsed_time(activation.activated_at).isoformat(),
             'expires_at': parsed_time(activation.expires_at).isoformat(),
             'cleared_at': cleared.isoformat(),
             'truth_role': 'protected validation only; excluded from model features'}
    output = persist(run_id, requests, recovery, events, window, features, truth, metadata)
    print('SERVICE-EFFECT DIAGNOSTIC CANDIDATE — NOT TRAINING DATA')
    print('RUN ID:', run_id, 'FAULT:', fault, 'TARGET:', target)
    print('IMPACT:', effect)
    print('RECOVERY:', recovery_proof)
    print('EXCLUSIVE OUTPUT:', output)
    return output


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    p.add_argument('--fault', required=True)
    p.add_argument('--target', required=True)
    p.add_argument('--rps', type=float, default=RATE)
    p.add_argument('--gateway-url', default='http://127.0.0.1:18110')
    p.add_argument('--acknowledge-diagnostic-only', action='store_true')
    args = p.parse_args(argv)
    allowed_configuration(args.fault, args.target, args.rps)
    url = safe_url(args.gateway_url)
    blocked_plan_gate()
    if args.dry_run:
        if args.acknowledge_diagnostic_only:
            raise ValueError('Acknowledgement only valid with live execution')
        print(f'DRY RUN: {args.fault} target={args.target} intensity={INTENSITY}, '
              f'{RATE} RPS, {DURATION_SECONDS}s, fault lease {LEASE_SECONDS}s')
        print('No Docker/network/fault activation/artifacts; 240 plan rows remain blocked')
        return 0
    if not args.acknowledge_diagnostic_only:
        raise ValueError('Live mode requires --acknowledge-diagnostic-only')
    asyncio.run(run_pilot(args.fault, args.target, url))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, ImportError, TypeError) as exc:
        print('SERVICE EFFECT PILOT STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
