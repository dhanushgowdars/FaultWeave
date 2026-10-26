#!/usr/bin/env python3
"""Phase 19C.2J: opt-in bounded, run-scoped downstream timeout diagnostic.

Only validates DOWNSTREAM_TIMEOUT on two dependency edges proven reachable in
healthy research traffic. It NEVER approves a draft row or produces training data.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2c_healthy_pilot import require_clean_local_stack, safe_url, proven_client_rows, visible_events  # noqa: E402
from scripts.phase19c2d_fault_pilot import (  # noqa: E402
    DURATION_SECONDS, LEASE_SECONDS, checked_rps, correlation_gate,
    new_plan, parsed_time, persist, recovery_gate,
)
from scripts.phase19c2i_readiness import (  # noqa: E402
    PARENT, PROPOSAL, strict_json, verify_blocked_matrix,
)

FAULT_ID = 'DOWNSTREAM_TIMEOUT'
INTENSITY = 'medium'
TARGETS = ('transaction->payment', 'payment->ledger')
SOURCE_TARGET_PATH = {
    'transaction->payment': '/internal/v1/payments',
    'payment->ledger': '/internal/v1/ledger',
}


def blocked_plan_gate(root: Path = ROOT) -> None:
    """The existence of the draft NEVER grants execution permission."""
    result = verify_blocked_matrix(strict_json(root / PARENT), strict_json(root / PROPOSAL))
    if result['blocked'] != 240:
        raise RuntimeError('STOP: blocked v2 plan invariants unexpectedly changed')


def timeout_impact(window, requests: list[dict], events: list[dict], target: str) -> dict:
    if target not in TARGETS:
        raise ValueError('Unapproved timeout edge')
    source, downstream = target.split('->')
    prefix = SOURCE_TARGET_PATH[target]
    identities = {r['request_id']: r['trace_id'] for r in requests
                  if window.started_at < parsed_time(r['started_at']) <= window.ended_at}
    affected = {
        r['request_id'] for r in requests
        if r['request_id'] in identities
        and (r.get('transport_error') is not None or r.get('status_code') is None
             or int(r['status_code']) >= 500)
    }
    timeout_events = {
        e['request_id'] for e in events
        if e.get('event_type') == 'downstream_request_failed'
        and e.get('service') == source
        and e.get('downstream_service') == downstream
        and str(e.get('path') or '').startswith(prefix)
        and str(e.get('error_type') or '').lower() in ('readtimeout', 'timeout', 'timeoutexception')
        and window.started_at < parsed_time(e['timestamp']) <= window.ended_at
        and e.get('request_id') in identities
        and e.get('trace_id') == identities[e['request_id']]
    }
    matching = timeout_events & affected
    if len(identities) < 60 or len(affected) < 10 or len(matching) < 5:
        raise RuntimeError('INVALID_TIMEOUT_EXPOSURE: insufficient correlated source->target '
                           f'timeouts (in_window={len(identities)}, affected={len(affected)}, '
                           f'timeouts_with_client_impact={len(matching)})')
    return {
        'in_window_requests': len(identities),
        'affected_client_requests': len(affected),
        'correlated_source_target_timeouts': len(timeout_events),
        'timeouts_with_affected_clients': len(matching),
        'edge': target,
    }


async def execute_bounded_timeout(store, run_id: str, target: str, plan: list,
                                  gateway_url: str, executor):
    """Lease guaranteed release even on cancellation. No infrastructure adapters."""
    if target not in TARGETS:
        raise ValueError('Invalid target')
    activation = store.acquire(
        run_id=run_id, fault_id=FAULT_ID, target=target,
        intensity=INTENSITY, duration_seconds=LEASE_SECONDS,
    )
    try:
        current = store.active()
        if current is None or current.activation_id != activation.activation_id:
            raise RuntimeError('Fault lease ownership mismatch')
        started = datetime.now(UTC)
        result = await executor(plan, gateway_url, run_id, 1905, 16)
        ended = datetime.now(UTC)
        if ended >= parsed_time(activation.expires_at):
            raise RuntimeError('Fault lease expired before capture ended')
    finally:
        store.release(activation.activation_id)
        cleared = datetime.now(UTC)
    return result, started, ended, activation, cleared


async def run_pilot(target: str, rps: float, gateway_url: str) -> Path:
    from datasets.phase19c2b_exact_windows import (
        V2WindowContractError, build_observable_features, select_exact_window,
    )
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import collect_run_events, execute_plan

    blocked_plan_gate()
    require_clean_local_stack()
    run_id = 'v2timeout-' + uuid4().hex[:16]
    store = FaultStateStore(ROOT / 'data' / 'fault-control')
    result, started, ended, activation, cleared = await execute_bounded_timeout(
        store, run_id, target, new_plan(run_id, rps), gateway_url, execute_plan,
    )
    require_clean_local_stack()
    recovered_raw = await execute_plan(new_plan(run_id, rps, recovery=True),
                                       gateway_url, run_id, 1906, 8)
    require_clean_local_stack()
    requests = proven_client_rows(result, run_id)
    recovery = proven_client_rows(recovered_raw, run_id)
    events = visible_events(collect_run_events(run_id), run_id)
    correlation_gate(requests + recovery, events)
    recovery_proof = recovery_gate(recovery, events, cleared_at=cleared)
    watermark = max(parsed_time(e['timestamp']) for e in events)
    try:
        window = select_exact_window(
            phase_started_at=started, phase_ended_at=ended,
            telemetry_watermark=watermark,
            fault_activated_at=activation.activated_at,
            fault_expires_at=activation.expires_at,
            fault_cleared_at=cleared,
            fault_required=True, lead_guard_seconds=2.0, tail_guard_seconds=2.0,
        )
        features = build_observable_features(
            run_id=run_id, window=window, requests=requests, events=events,
            require_boundary_evidence=True,
        )
    except V2WindowContractError as exc:
        raise RuntimeError(f'INVALID TIMEOUT WINDOW: {exc}') from exc
    impact = timeout_impact(window, requests, events, target)
    metadata = {
        'phase_started_at': started.isoformat(),
        'phase_ended_at': ended.isoformat(),
        'telemetry_watermark': watermark.isoformat(),
        'scheduled_rps': rps,
        'scheduled_requests': len(new_plan(run_id, rps)),
        'observed_requests': len(requests), 'observed_events': len(events),
        'recovery': recovery_proof, 'impact': impact,
        'passed_pre_post_stack_health': True,
        'source_script': 'scripts/phase19c2j_timeout_pilot.py',
        'training_eligibility': False,
        'feature_schema_version': '2.0-candidate-unchanged-65',
    }
    truth = {
        'schema_version': '19c2j.timeout.1', 'run_id': run_id,
        'fault_id': FAULT_ID, 'target': target, 'intensity': INTENSITY,
        'activated_at': parsed_time(activation.activated_at).isoformat(),
        'expires_at': parsed_time(activation.expires_at).isoformat(),
        'cleared_at': cleared.isoformat(),
        'truth_role': 'post-observation validity only; not model feature input',
    }
    out = persist(run_id, requests, recovery, events, window, features, truth, metadata)
    print('TIMEOUT DIAGNOSTIC CANDIDATE — NOT TRAINING DATA')
    print('RUN ID:', run_id)
    print('WINDOW:', window.started_at.isoformat(), '->', window.ended_at.isoformat())
    print('IMPACT:', impact)
    print('RECOVERY:', recovery_proof)
    print('EXCLUSIVE OUTPUT:', out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true')
    group.add_argument('--execute', action='store_true')
    parser.add_argument('--target', choices=TARGETS, required=True)
    parser.add_argument('--rps', type=float, default=1.5)
    parser.add_argument('--gateway-url', default='http://127.0.0.1:18110')
    args = parser.parse_args(argv)
    rate = checked_rps(args.rps)
    if rate not in (1.5, 2.0):
        raise ValueError('Only previously tested diagnostic rates 1.5 or 2.0 are allowed')
    url = safe_url(args.gateway_url)
    blocked_plan_gate()
    if args.dry_run:
        print(f'DRY RUN ONLY: {FAULT_ID} {args.target}, intensity {INTENSITY}, '
              f'{DURATION_SECONDS}s, {rate} RPS, lease {LEASE_SECONDS}s')
        print('Original 240 planned runs remain BLOCKED; diagnostic pilot only')
        print('No Docker/network/fault activation, artifacts, model, or Git operations')
        return 0
    asyncio.run(run_pilot(args.target, rate, url))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print('PHASE 19C.2J STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
