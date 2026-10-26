"""Isolated-only bounded healthy telemetry feeder. Research use; no faults or inference.

Unlike the single-use V2 bridge, this process streams newly observed client
requests and their correlated service logs in separate create-once batches.
It refuses to mix with recent in-memory gateway data. It NEVER resets Docker,
clears telemetry, touches original gateway, retrains or calls the ML model.

--preview: no I/O; --preflight: read-only checks; --feed: explicit opt-in,
225 isolated research transactions (150s @1.5RPS) and live telemetry POSTs.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from scripts import faultweave_v2_isolated_telemetry_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.runtime' / 'v2-rolling-feeder'
GATEWAY = bridge.ISOLATED
RATE = 1.5
COUNT = 225
POLL_SECONDS = 5
MIN_PRIOR_AGE_SECONDS = 200


def fail(msg: str) -> None:
    bridge.fail(msg)


def no_recent_data(*, now: datetime | None = None) -> str:
    """Require gateway either truly empty or stale enough to prune old data.

    With MAX_BUFFER_SECONDS=180, events >200s old cannot enter the new window,
    and will be pruned on the first new event. Fail on unknown 409 reasons.
    """
    from services.gateway.app.intelligence_live import MAX_BUFFER_SECONDS
    if MAX_BUFFER_SECONDS != 180:
        fail('live gateway retention changed; rolling feeder not reviewed')
    code, obj = bridge._request_json(f'{GATEWAY}/api/v1/intelligence/live/features/60')
    if code == 409 and obj.get('detail') == 'no live telemetry has been observed':
        return 'EMPTY'
    if code != 200:
        fail(f'gateway buffer state ambiguous: HTTP {code}, {str(obj.get("detail"))[:150]}')
    end = bridge.utc(obj.get('window_ended_at'))
    age = ((now or datetime.now(UTC)) - end).total_seconds()
    if age < MIN_PRIOR_AGE_SECONDS:
        fail(f'existing telemetry watermark is only {age:.1f}s old; no run mixing or gateway reset allowed')
    if obj.get('request_observation_source') != 'client_observed':
        fail('previous gateway buffer has untrusted client observation provenance')
    return f'STALE_PRIOR_BUFFER({age:.1f}s)'


def preflight() -> None:
    if (OUT / 'active.lock').exists() or (OUT / 'active.lock').is_symlink():
        fail('prior rolling feeder lock remains; investigate before rerunning')
    from scripts import faultweave_v2_isolated_db as isolated
    from scripts.faultweave_v2_isolated_unknown_capture import validate_primary_stack
    from scripts.faultweave_v2_candidate04_blind_eval import verify_frozen
    from datasets.final_features import FEATURE_NAMES
    from faultweave_common.logging import StructuredLogEvent
    from services.gateway.app.intelligence_live import LiveRequestObservation
    verify_frozen()
    isolated.inspect_healthy()
    validate_primary_stack()
    for path in (isolated.FIXTURE / 'fault-control/active_fault.json',
                 ROOT / 'data/fault-control/active_fault.json'):
        if path.exists() or path.is_symlink():
            fail('fault lease is active; never run healthy experiment under a fault')
    if not hasattr(StructuredLogEvent, 'model_validate') or not hasattr(LiveRequestObservation, 'model_validate'):
        fail('local request/event schemas unavailable')
    ready = bridge.require_200(f'{bridge.SHADOW}/api/v2/candidate04/ready')
    if (ready.get('status') != 'READY_FOR_RESEARCH_INFERENCE' or
        ready.get('model_sha256') != bridge.MODEL_SHA or
        ready.get('deployment_status') != 'RESEARCH_ONLY_NOT_DEPLOYED'):
        fail('shadow model identity changed')
    schema = bridge.require_200(f'{bridge.SHADOW}/api/v2/candidate04/schema')
    bridge.contract(schema.get('feature_names'))
    if len(FEATURE_NAMES) != 65:
        fail('V2 feature contract changed')
    state = no_recent_data()
    print('PASS: frozen model, isolated and original stacks healthy, fault leases idle')
    print('PASS: 65-feature shadow contract; isolated buffer state:', state)
    print('NO traffic, telemetry POST, Docker changes, model writes or inference')


def hash_event(raw: dict, run_id: str) -> str:
    """Canonical feature-visible event identity, consistent with V2 bridge."""
    from scripts.phase19c2c_healthy_pilot import visible_events
    event = visible_events([raw], run_id)[0]
    return json.dumps(event, sort_keys=True, separators=(',', ':'))


def new_rows(raw: list[dict], observed_requests: dict[str, dict], run_id: str,
             posted_events: set[str], posted_requests: set[str]) -> tuple[list[dict], list[dict], set[str], set[str]]:
    """Build a prospective batch without mutating offsets before the POST succeeds."""
    from scripts.phase19c2c_healthy_pilot import proven_client_rows
    events, candidate_event_keys = [], set()
    linked: dict[str, set[str]] = {}
    from services.gateway.app.intelligence_live import eligible_runtime_event
    for item in raw:
        if not eligible_runtime_event(item):
            continue
        key = hash_event(item, run_id)
        rid = str(item.get('request_id') or '')
        trace = str(item.get('trace_id') or '')
        linked.setdefault(rid, set()).add(trace)
        if key not in posted_events and key not in candidate_event_keys:
            events.append(bridge.event_wire(item, run_id))
            candidate_event_keys.add(key)
    requests, candidate_request_ids = [], set()
    for rid, obs in observed_requests.items():
        if rid in posted_requests:
            continue
        row = proven_client_rows([obs], run_id)[0]
        if row.get('expected_outcome') is not True or row.get('status_code') != 200 or row.get('transport_error'):
            fail('a healthy research request failed; preserve evidence and stop')
        # Only post client observations once the corresponding Docker events are observed.
        if rid not in linked:
            continue
        if linked[rid] != {row['trace_id']}:
            fail('request-to-service trace mismatch; do not ingest')
        requests.append(bridge.request_wire(row, run_id))
        candidate_request_ids.add(rid)
    # All payloads have been normalized and validated before the first POST.
    return events, requests, candidate_event_keys, candidate_request_ids


def post_once(events: list[dict], requests: list[dict]) -> None:
    if not events and not requests:
        return
    # The live endpoint permits <=500 items per array, but use smaller batches.
    rows = [(bridge.utc(e['timestamp']), 'event', e) for e in events]
    rows.extend((bridge.utc(r['started_at']), 'request', r) for r in requests)
    rows.sort(key=lambda x: x[0])
    for start in range(0, len(rows), 200):
        segment = rows[start:start + 200]
        batch = {'events': [record for _, kind, record in segment if kind == 'event'],
                 'requests': [record for _, kind, record in segment if kind == 'request']}
        result = bridge.require_200(f'{GATEWAY}/api/v1/intelligence/telemetry', batch)
        if (result.get('status') != 'accepted' or result.get('ignored') or
            result.get('duplicates') or result.get('duplicate_requests') or
            result.get('accepted') != len(batch['events']) or
            result.get('accepted_requests') != len(batch['requests'])):
            fail('telemetry POST partially accepted/duplicated; stop without retries')


async def feed() -> None:
    """Bounded streaming pilot; one run ID, independent client provenance."""
    from experiments.runner import PlannedRequest, execute_plan
    from scripts.faultweave_v2_final_two_capture import raw_events_for
    from scripts.phase19c2c_healthy_pilot import proven_client_rows, visible_events, validate_correlations
    from scripts import faultweave_v2_isolated_db as iso
    from scripts.faultweave_v2_isolated_unknown_capture import validate_primary_stack
    from datasets.phase19c2b_exact_windows import ExactWindow, build_observable_features

    preflight()
    run_id = 'v2rolling-' + uuid4().hex[:16]
    # Create-only lock protects against two active collectors sharing the live buffer.
    lock = OUT / 'active.lock'
    bridge.write_new(lock, {'run_id': run_id, 'started_at': datetime.now(UTC).isoformat()})
    intent = OUT / (run_id + '.intent.json')
    bridge.write_new(intent, {'run_id': run_id, 'status': 'STARTED',
        'gateway': GATEWAY, 'training_eligible': False, 'threshold_tuning_eligible': False,
        'created_at': datetime.now(UTC).isoformat()})
    observed: dict[str, dict] = {}
    posted_events: set[str] = set()
    posted_requests: set[str] = set()
    event_total = request_total = 0

    async def observer(row: dict) -> None:
        rid = row.get('request_id')
        if not isinstance(rid, str) or not rid.startswith(run_id + '-request-') or rid in observed:
            fail('bad or duplicate observed client request ID')
        observed[rid] = row

    # No traffic generated until preflight, intent and buffers pass.
    no_recent_data()
    plan = [PlannedRequest(sequence=i+1, offset_seconds=i/RATE,
                           scenario='valid', amount_minor=4000+i%100) for i in range(COUNT)]
    task = asyncio.create_task(execute_plan(plan, GATEWAY, run_id, 3304, 16,
                                            result_observer=observer))
    print('ROLLING RUN:', run_id, flush=True)
    try:
        while not task.done():
            await asyncio.sleep(POLL_SECONDS)
            raw = await asyncio.to_thread(raw_events_for, run_id, True)
            events, requests, event_keys, request_ids = new_rows(
                raw, observed, run_id, posted_events, posted_requests)
            post_once(events, requests)
            posted_events.update(event_keys)
            posted_requests.update(request_ids)
            event_total += len(events)
            request_total += len(requests)
            print('INGESTED:', f'events={event_total}', f'requests={request_total}', flush=True)
        results = await task
        # Allow tail events to flush, but never conceal absent correlations.
        await asyncio.sleep(2)
        raw = await asyncio.to_thread(raw_events_for, run_id, True)
        rows = proven_client_rows(results, run_id)
        if len(rows) != COUNT or len(observed) != COUNT:
            fail('incomplete source client requests')
        visible = visible_events(raw, run_id)
        validate_correlations(rows, visible)
        events, requests, event_keys, request_ids = new_rows(
            raw, observed, run_id, posted_events, posted_requests)
        post_once(events, requests)
        posted_events.update(event_keys)
        posted_requests.update(request_ids)
        event_total += len(events)
        request_total += len(requests)
        if request_total != COUNT:
            fail('incomplete client provenance ingestion')
        iso.inspect_healthy()
        validate_primary_stack()
        for path in (iso.FIXTURE / 'fault-control/active_fault.json',
                     ROOT / 'data/fault-control/active_fault.json'):
            if path.exists() or path.is_symlink():
                fail('fault lease appeared during bounded healthy feed')
        snapshot = bridge.require_200(f'{GATEWAY}/api/v1/intelligence/live/features/60')
        end = bridge.utc(snapshot['window_ended_at'])
        window = ExactWindow(started_at=end - timedelta(seconds=60), ended_at=end)
        canonical = build_observable_features(run_id=run_id, window=window,
            requests=rows, events=visible, require_boundary_evidence=True)
        bridge.strict_comparison(snapshot, canonical, window)
        sha = hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False).encode()).hexdigest()
        path = OUT / (run_id + '.result.json')
        bridge.write_new(path, {'run_id': run_id, 'status': 'VALIDATED_BOUNDED_ROLLING_FEED',
            'training_eligible': False, 'threshold_tuning_eligible': False,
            'gateway': GATEWAY, 'transactions_generated': COUNT,
            'posted_client_requests': request_total, 'posted_events': event_total,
            'window_seconds': 60, 'window_started_at': window.started_at.isoformat(),
            'window_ended_at': window.ended_at.isoformat(),
            'window_request_count': snapshot['request_count'],
            'window_event_count': snapshot['event_count'],
            'feature_vector_sha256': sha, 'model_sha256': bridge.MODEL_SHA,
            'ml_inference_performed': False})
        print('PASS: exact 65 V2 feature values match live gateway', flush=True)
        print('ROLLING FEED RECEIPT:', path.relative_to(ROOT), flush=True)
        print('NO model inference, fault injection, original gateway writes or Docker changes')
        # Release lock only after a complete, verified receipt is committed.
        lock.unlink()
    except BaseException:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        print('STOP: rolling feed incomplete; do not retry or clear gateway buffer', file=sys.stderr)
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--preview', action='store_true')
    g.add_argument('--preflight', action='store_true')
    g.add_argument('--feed', action='store_true')
    p.add_argument('--acknowledge-225-isolated-research-transactions-and-telemetry', action='store_true')
    a = p.parse_args(argv)
    if a.preview:
        print('READ ONLY: bounded rolling healthy telemetry feeder')
        print('225 isolated research requests over ~150s, live posts every 5s')
        print('No original stack changes, faults, model inference, training writes, or DB resets')
        print('Requires prior gateway buffer empty or old enough to prune (>200s)')
        return 0
    if a.preflight:
        preflight()
        return 0
    if not a.acknowledge_225_isolated_research_transactions_and_telemetry:
        fail('explicit consent required: 225 genuine isolated transactions and streamed telemetry posts')
    asyncio.run(feed())
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('ROLLING FEED STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
