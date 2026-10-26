#!/usr/bin/env python3
"""Phase 19C.2L: bounded authentication-rejection *diagnostic*, never training data.

The AUTHENTICATION_FAILURE_BURST mechanism is invalid-login research traffic,
not server-side injected errors. This 1.5 RPS path/recovery validation does NOT
qualify the legacy 20-60 RPS burst intensity or authorize planned v2 runs.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2c_healthy_pilot import (  # noqa: E402
    REQUEST_FIELDS, json_bytes, jsonl_bytes, proven_client_rows,
    require_clean_local_stack, safe_url, visible_events,
)
from scripts.phase19c2d_fault_pilot import (  # noqa: E402
    DURATION_SECONDS, LEASE_SECONDS, RECOVERY_REQUESTS, correlation_gate,
    new_plan, parsed_time, recovery_gate,
)
from scripts.phase19c2j_timeout_pilot import blocked_plan_gate  # noqa: E402

SCHEMA = '19c2l.auth-rejection.1'
FAULT_ID = 'AUTHENTICATION_FAILURE_BURST'
TARGET = 'authentication'
INTENSITY = 'mild'
RPS = 1.5
OUT_DIR = ROOT / 'data/datasets/v2/pilot-fault'


def auth_plan(run_id: str):
    """Re-use the real research executor; change only scenario to invalid_login."""
    return [replace(p, scenario='invalid_login') for p in new_plan(run_id, RPS)]


def auth_rows(raw: list[dict], run_id: str) -> list[dict]:
    """Never reuse healthy-only provenance validator for 401 responses."""
    rows = []
    seen = set()
    for item in raw:
        rid = item.get('request_id')
        if (item.get('scenario') != 'invalid_login' or not isinstance(rid, str) or
            not rid.startswith(run_id + '-request-') or rid in seen or
            not item.get('trace_id') or not item.get('started_at')):
            raise ValueError('Invalid newly issued invalid-login client provenance')
        seen.add(rid)
        rows.append({'run_id': run_id, **{k: item.get(k) for k in REQUEST_FIELDS if k != 'run_id'}})
    if not rows:
        raise ValueError('No invalid-login requests collected')
    return rows


def auth_impact(window, requests: list[dict], events: list[dict]) -> dict:
    """Require real client 401s and independently correlated authentication 401 logs."""
    in_window = {r['request_id']: r for r in requests
                 if window.started_at < parsed_time(r['started_at']) <= window.ended_at}
    rejected = {rid for rid, row in in_window.items()
                if row.get('status_code') == 401 and row.get('expected_outcome') is True
                and row.get('transport_error') is None and row.get('scenario') == 'invalid_login'}
    auth_logs = {e.get('request_id') for e in events
                 if e.get('service') == 'authentication'
                 and e.get('event_type') == 'http_request_completed'
                 and e.get('status_code') == 401
                 and e.get('request_id') in rejected
                 and e.get('trace_id') == in_window[e['request_id']]['trace_id']
                 and window.started_at < parsed_time(e['timestamp']) <= window.ended_at}
    if len(in_window) < 60 or len(rejected) < 0.9 * len(in_window):
        raise RuntimeError(f'AUTH_REJECTION_NOT_PROVEN: in_window={len(in_window)} rejected={len(rejected)}')
    if len(auth_logs) < max(5, int(0.75 * len(rejected))):
        raise RuntimeError(f'AUTH_SERVICE_LOGS_NOT_PROVEN: rejected={len(rejected)} auth_401={len(auth_logs)}')
    return {'in_window_requests': len(in_window), 'client_401_rejections': len(rejected),
            'correlated_authentication_401_requests': len(auth_logs),
            'target': TARGET, 'rate_rps': RPS, 'high_rate_burst_proven': False}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def persist(run_id: str, requests: list[dict], recovery: list[dict], events: list[dict],
            window, vector: dict, truth: dict, meta: dict) -> Path:
    from datasets.final_features import FEATURE_NAMES
    if len(FEATURE_NAMES) != 65 or tuple(vector) != FEATURE_NAMES:
        raise RuntimeError('Unapproved v2 feature schema')
    candidate = {'schema_version': SCHEMA, 'run_id': run_id,
                 'split': 'diagnostic_only', 'training_eligible': False,
                 'threshold_tuning_eligible': False, 'label': None,
                 'candidate_status': 'UNLABELED_AUTH_REJECTION_DIAGNOSTIC',
                 'feature_count': 65, 'window_seconds': 60,
                 'window_started_at': window.started_at.isoformat(),
                 'window_ended_at': window.ended_at.isoformat(), 'features': vector}
    payload = {'requests.jsonl': jsonl_bytes(requests),
               'recovery_requests.jsonl': jsonl_bytes(recovery),
               'events.jsonl': jsonl_bytes(events),
               'candidate_feature.json': json_bytes(candidate),
               'protected_ground_truth.json': json_bytes(truth)}
    manifest = {'schema_version': SCHEMA, 'run_id': run_id,
                'intent': 'bounded low-rate invalid-login path diagnostic; not burst capacity validation',
                'split': 'diagnostic_only', 'training_eligible': False,
                'threshold_tuning_eligible': False, 'outgoing_header': 'X-Run-ID',
                'client_provenance': 'experiments.runner.execute_plan',
                'feature_schema_sha256': sha('\n'.join(FEATURE_NAMES).encode()),
                'window_started_at': window.started_at.isoformat(),
                'window_ended_at': window.ended_at.isoformat(),
                'metadata': meta,
                'artifacts': {n: {'sha256': sha(b), 'bytes': len(b)} for n, b in payload.items()}}
    payload['manifest.json'] = json_bytes(manifest)
    out = OUT_DIR / run_id
    if out.exists() or out.is_symlink():
        raise RuntimeError('STOP: create-only destination already exists')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(exist_ok=False)
    for name, content in payload.items():
        with (out / name).open('xb') as f:
            f.write(content)
    return out


async def execute_auth_phase(store, run_id: str, plan: list, url: str, executor):
    """Exclusive lease is for run isolation; rejected logins are normal auth behavior."""
    activation = store.acquire(run_id=run_id, fault_id=FAULT_ID, target=TARGET,
                               intensity=INTENSITY, duration_seconds=LEASE_SECONDS)
    try:
        current = store.active()
        if current is None or current.activation_id != activation.activation_id:
            raise RuntimeError('Exclusive authentication diagnostic lease not owned')
        started = datetime.now(UTC)
        raw = await executor(plan, url, run_id, 1907, 16)
        ended = datetime.now(UTC)
        if ended >= parsed_time(activation.expires_at):
            raise RuntimeError('Authentication diagnostic lease expired')
    finally:
        store.release(activation.activation_id)
        cleared = datetime.now(UTC)
    return raw, started, ended, activation, cleared


async def run_pilot(url: str) -> Path:
    from datasets.phase19c2b_exact_windows import (  # noqa: E402
        V2WindowContractError, build_observable_features, select_exact_window,
    )
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import collect_run_events, execute_plan

    blocked_plan_gate()
    require_clean_local_stack()
    run_id = 'v2auth-' + uuid4().hex[:16]
    plan = auth_plan(run_id)
    store = FaultStateStore(ROOT / 'data/fault-control')
    raw, started, ended, activation, cleared = await execute_auth_phase(
        store, run_id, plan, url, execute_plan)
    require_clean_local_stack()
    recovery_raw = await execute_plan(new_plan(run_id, RPS, recovery=True), url, run_id, 1908, 8)
    require_clean_local_stack()
    requests = auth_rows(raw, run_id)
    recovery = proven_client_rows(recovery_raw, run_id)
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
            fault_required=True, lead_guard_seconds=2.0, tail_guard_seconds=2.0)
        vector = build_observable_features(run_id=run_id, window=window,
                                           requests=requests, events=events,
                                           require_boundary_evidence=True)
    except V2WindowContractError as exc:
        raise RuntimeError(f'INVALID_AUTH_WINDOW: {exc}') from exc
    impact = auth_impact(window, requests, events)
    meta = {'phase_started_at': started.isoformat(), 'phase_ended_at': ended.isoformat(),
            'telemetry_watermark': watermark.isoformat(), 'scheduled_rps': RPS,
            'scheduled_requests': len(plan), 'observed_requests': len(requests),
            'observed_events': len(events), 'impact': impact, 'recovery': recovery_proof,
            'passed_pre_post_stack_health': True,
            'source_script': 'scripts/phase19c2l_auth_pilot.py',
            'feature_schema_version': '2.0-candidate-unchanged-65',
            'high_rate_auth_burst_validation': False}
    truth = {'schema_version': SCHEMA, 'run_id': run_id, 'fault_id': FAULT_ID,
             'target': TARGET, 'intensity': INTENSITY,
             'activated_at': parsed_time(activation.activated_at).isoformat(),
             'expires_at': parsed_time(activation.expires_at).isoformat(),
             'cleared_at': cleared.isoformat(),
             'truth_role': 'diagnostic validity only; prohibited from model feature input',
             'high_rate_burst_proven': False}
    out = persist(run_id, requests, recovery, events, window, vector, truth, meta)
    print('AUTH REJECTION DIAGNOSTIC CANDIDATE — NOT TRAINING DATA')
    print('RUN ID:', run_id)
    print('WINDOW:', window.started_at.isoformat(), '->', window.ended_at.isoformat())
    print('IMPACT:', impact)
    print('RECOVERY:', recovery_proof)
    print('EXCLUSIVE OUTPUT:', out)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    p.add_argument('--rps', type=float, default=RPS)
    p.add_argument('--gateway-url', default='http://127.0.0.1:18110')
    args = p.parse_args(argv)
    if args.rps != RPS:
        raise ValueError('Only diagnostic 1.5 RPS allowed; no 20/40/60 RPS burst approval')
    url = safe_url(args.gateway_url)
    blocked_plan_gate()
    if args.dry_run:
        print(f'DRY RUN ONLY: invalid-login rejection path, {RPS} RPS, {DURATION_SECONDS}s, '
              f'lease {LEASE_SECONDS}s, {RECOVERY_REQUESTS} recovery requests')
        print('HIGH-RATE AUTHENTICATION_FAILURE_BURST NOT CALIBRATED; 240 runs BLOCKED')
        print('No Docker/network/fault activation, artifacts, models or Git operations')
        return 0
    asyncio.run(run_pilot(url))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print('PHASE 19C.2L STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
