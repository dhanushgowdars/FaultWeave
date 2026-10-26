"""Safe, opt-in V2 live telemetry bridge pilot. Does not modify the gateway or model.

--preview: local text only
--preflight: READ-ONLY checks of both Docker projects, model, gateway, shadow API
--capture: ONLY with explicit ack; 75s healthy research requests to ISOLATED stack,
           ingest provenance-checked structured events + client observations into
           isolated gateway's volatile live buffer, compare canonical V2 vector,
           then send exact 65-feature window to Candidate04 shadow API.

Not a continuous daemon or production integration. Never injects faults, edits
model/training data, restarts containers, or touches the original gateway.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'shared'
if SHARED.is_dir() and str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))
ISOLATED = 'http://127.0.0.1:28110'
SHADOW = 'http://127.0.0.1:28190'
ARTIFACTS = ROOT / '.runtime' / 'v2-live-bridge'
RATE = 1.5
DURATION = 75.0
COUNT = 113
MODEL_SHA = '7bbf6c2b0ba57d40a45d1ac633c3b50ecfe9ed1b2ea6354d460a32fd120da0be'
RUN_RE = re.compile(r'^v2bridge-[0-9a-f]{16}$')
EVENT_SAFE_FIELDS = frozenset({
    'schema_version', 'timestamp', 'service', 'environment', 'level',
    'event_type', 'message', 'run_id', 'request_id', 'trace_id',
    'outcome', 'success', 'method', 'path', 'status_code', 'latency_ms',
    'error_type', 'downstream_service', 'error_code',
})
REQUEST_SAFE_FIELDS = (
    'request_id', 'trace_id', 'started_at', 'latency_ms', 'status_code',
    'expected_outcome', 'transport_error', 'scenario',
)


def fail(message: str) -> None:
    raise RuntimeError('STOP: ' + message)


def utc(value: str | datetime) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if dt.tzinfo is None:
        fail('timezone missing from telemetry timestamp')
    return dt.astimezone(UTC)


def _request_json(url: str, payload: dict | None = None, timeout: int = 20) -> tuple[int, dict]:
    """Only fixed loopback hosts, JSON and short timeouts; never follows redirects."""
    if not any(url == prefix or url.startswith(prefix + '/') for prefix in (ISOLATED, SHADOW)):
        fail('unapproved HTTP target')
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode('utf-8')
    request = Request(url, data=data, method='GET' if payload is None else 'POST',
                      headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    # Redirects disallowed: urllib default redirect handler follows GET redirects.
    from urllib.request import build_opener, HTTPHandler, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with build_opener(HTTPHandler(), NoRedirect()).open(request, timeout=timeout) as resp:
            raw, code = resp.read(1024 * 1024), resp.status
    except HTTPError as exc:
        code, raw = exc.code, exc.read(1024 * 1024)
    except (OSError, URLError) as exc:
        fail(f'localhost API unreachable: {type(exc).__name__}: {exc}')
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        fail(f'non-JSON API response HTTP {code}: {exc}')
    if not isinstance(obj, dict):
        fail('API returned a non-object JSON response')
    return code, obj


def require_200(url: str, payload: dict | None = None, timeout: int = 20) -> dict:
    code, obj = _request_json(url, payload, timeout)
    if code != 200:
        fail(f'HTTP {code} from local API {url}: {str(obj.get("detail", ""))[:200]}')
    return obj


def require_empty_buffer() -> None:
    """Never mix this run with earlier telemetry; fail instead of resetting gateway."""
    for width in (10, 60):
        code, payload = _request_json(f'{ISOLATED}/api/v1/intelligence/live/features/{width}')
        if code != 409 or payload.get('detail') != 'no live telemetry has been observed':
            fail(f'isolated gateway buffer is not provably empty ({width}s HTTP {code}; '
                 f'{str(payload.get("detail"))[:150]}); restart/clear ONLY after a separate review')


def contract(names: list[str]) -> None:
    from datasets.final_features import FEATURE_NAMES
    if len(names) != 65 or names != list(FEATURE_NAMES):
        fail('live bridge / shadow Candidate04 feature names or order mismatch')


def preflight() -> None:
    from scripts import faultweave_v2_isolated_db as iso
    from scripts.faultweave_v2_candidate04_blind_eval import verify_frozen
    from scripts.faultweave_v2_isolated_unknown_capture import validate_primary_stack
    from datasets.final_features import FEATURE_NAMES
    verify_frozen()
    # Validate local event/request schema availability before generating 75s of traffic.
    from faultweave_common.logging import StructuredLogEvent
    from services.gateway.app.intelligence_live import LiveRequestObservation
    if not hasattr(StructuredLogEvent, 'model_validate') or not hasattr(LiveRequestObservation, 'model_validate'):
        fail('local structured logging or live request schema is unavailable')
    iso.inspect_healthy()
    validate_primary_stack()
    for file in (iso.FIXTURE / 'fault-control/active_fault.json',
                 ROOT / 'data/fault-control/active_fault.json'):
        if file.exists() or file.is_symlink():
            fail(f'fault lease exists at {file}; no healthy pilot under a fault')
    ready = require_200(f'{SHADOW}/api/v2/candidate04/ready')
    if (ready.get('status') != 'READY_FOR_RESEARCH_INFERENCE' or
        ready.get('model_sha256') != MODEL_SHA or
        ready.get('feature_count') != 65 or
        ready.get('deployment_status') != 'RESEARCH_ONLY_NOT_DEPLOYED'):
        fail('shadow API is not frozen Candidate04 research service')
    schema = require_200(f'{SHADOW}/api/v2/candidate04/schema')
    if schema.get('feature_count') != 65:
        fail('shadow schema is not 65')
    # Shadow's schema field is `feature_names` in the locally tested API.
    contract(schema.get('feature_names'))
    require_empty_buffer()
    if len(FEATURE_NAMES) != 65:
        fail('local 65-feature schema changed')
    print('PASS: frozen model, two healthy isolated/primary stacks, leases idle, APIs ready')
    print('PASS: gateway live telemetry buffer is empty; 65-feature schema matches')
    print('NO traffic, telemetry POST, Docker changes or training/model writes')


def event_wire(raw: dict, run_id: str) -> dict:
    """Strip arbitrary attributes/messages of sensitive contents before posting.

    Validate with the project's actual StructuredLogEvent schema, so failures
    occur BEFORE any telemetry HTTP POST.
    """
    if raw.get('run_id') != run_id:
        fail('cross-run event detected')
    if raw.get('service') not in (
        'gateway', 'authentication', 'transaction', 'payment', 'account', 'ledger'):
        fail('unexpected service in correlated event')
    filtered = {k: raw[k] for k in EVENT_SAFE_FIELDS if k in raw}
    # Message/attributes aren't ML features. Use a fixed non-sensitive description.
    if 'message' in filtered:
        filtered['message'] = 'Observed structured microservice event'
    from faultweave_common.logging import StructuredLogEvent
    if 'attributes' in StructuredLogEvent.model_fields:
        filtered['attributes'] = {}  # always redact arbitrary structured attributes
    model = StructuredLogEvent.model_validate(filtered)
    projected = model.model_dump(mode='json', exclude_none=False)
    if projected.get('run_id') != run_id:
        fail('schema-normalized event lost run provenance')
    return projected


def request_wire(raw: dict, run_id: str) -> dict:
    if raw.get('run_id') != run_id:
        fail('cross-run client request detected')
    if raw.get('scenario') != 'valid':
        fail('this pilot only permits healthy valid research requests')
    result = {name: raw.get(name) for name in REQUEST_SAFE_FIELDS}
    from services.gateway.app.intelligence_live import LiveRequestObservation  # optional direct local check
    return LiveRequestObservation.model_validate(result).model_dump(mode='json')


def slice_window(requests: list[dict], events: list[dict], started: datetime, finished: datetime, run_id: str):
    """Choose event-aligned ending time, require independent 60-second evidence."""
    from datasets.phase19c2b_exact_windows import ExactWindow, build_observable_features
    from services.gateway.app.intelligence_live import eligible_runtime_event
    eligible = [e for e in events if eligible_runtime_event(e)]
    before = finished - timedelta(seconds=2)
    candidates = [utc(e['timestamp']) for e in eligible if utc(e['timestamp']) <= before]
    if not candidates:
        fail('no eligible service events before phase tail guard')
    end = max(candidates)
    if end - timedelta(seconds=60) < started + timedelta(seconds=2):
        fail('insufficient eligible phase coverage to support exact 60s window')
    window = ExactWindow(started_at=end - timedelta(seconds=60), ended_at=end)
    feature = build_observable_features(run_id=run_id, window=window,
        requests=requests, events=events, require_boundary_evidence=True)
    selected = [e for e in events if eligible_runtime_event(e) and utc(e['timestamp']) <= end]
    if not any(utc(e['timestamp']) <= window.started_at for e in selected):
        fail('no leading service event to prove 60s live watermark coverage')
    # timestamps checked for unrealistic clock skew / an incomplete run
    if (datetime.now(UTC) - end).total_seconds() > 90:
        fail('source telemetry is stale, cannot call this live inference')
    return window, feature, selected


def strict_comparison(snapshot: dict, expected: dict, window) -> None:
    from datasets.final_features import FEATURE_NAMES
    if snapshot.get('window_seconds') != 60 or snapshot.get('request_observation_source') != 'client_observed':
        fail('live snapshot did not use 60s independently observed client requests')
    if utc(snapshot['window_ended_at']) != window.ended_at or utc(snapshot['window_started_at']) != window.started_at:
        fail('gateway window misaligned with canonical V2 evidence')
    if float(snapshot.get('coverage_seconds', 0)) < 60:
        fail('gateway buffer lacks 60s of event evidence')
    actual = snapshot.get('features')
    if not isinstance(actual, dict) or set(actual) != set(FEATURE_NAMES):
        fail('gateway live feature names differ from the frozen model')
    differences = [name for name in FEATURE_NAMES
                   if type(actual[name]) not in (int, float)
                   or not math.isfinite(actual[name])
                   or float(actual[name]) != float(expected[name])]
    if differences:
        fail('LIVE_V2_FEATURE_DRIFT: ' + ', '.join(differences[:15]) +
             f' ({len(differences)} fields differ); no model inference')
    if int(snapshot.get('request_count', 0)) < 70 or int(snapshot.get('event_count', 0)) < 70:
        fail('too few independently observed requests or service events')


def ingest(events: list[dict], requests: list[dict]) -> None:
    """No POST occurs until every payload was validated before calling this."""
    rows = [(utc(x['timestamp']), 'event', x) for x in events]
    rows += [(utc(x['started_at']), 'request', x) for x in requests]
    rows.sort(key=lambda x: x[0])
    accepted_events = accepted_requests = 0
    for start in range(0, len(rows), 250):
        part = rows[start:start+250]
        body = {'events': [x for _, k, x in part if k == 'event'],
                'requests': [x for _, k, x in part if k == 'request']}
        outcome = require_200(f'{ISOLATED}/api/v1/intelligence/telemetry', body)
        if outcome.get('status') != 'accepted' or outcome.get('duplicates') or outcome.get('duplicate_requests'):
            fail('gateway ingestion refused or deduplicated run events; stop, no auto-retry')
        if outcome.get('accepted') != len(body['events']) or outcome.get('accepted_requests') != len(body['requests']):
            fail('gateway unexpectedly filtered telemetry; do not infer')
        accepted_events += outcome['accepted']
        accepted_requests += outcome['accepted_requests']
    if accepted_events != len(events) or accepted_requests != len(requests):
        fail('telemetry ingestion was incomplete')
    print(f'PASS: posted real, run-scoped events={accepted_events} requests={accepted_requests}')


def write_new(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        fail('refusing to overwrite telemetry audit record')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(path, flags, 0o600), 'w', encoding='utf-8') as out:
        json.dump(payload, out, sort_keys=True, indent=2, allow_nan=False)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())


async def capture() -> None:
    from experiments.runner import PlannedRequest, execute_plan
    from scripts.phase19c2c_healthy_pilot import proven_client_rows, validate_correlations, visible_events
    from scripts.faultweave_v2_final_two_capture import raw_events_for
    from services.gateway.app.intelligence_live import eligible_runtime_event
    from datasets.final_features import FEATURE_NAMES
    preflight()
    run_id = 'v2bridge-' + uuid4().hex[:16]
    if not RUN_RE.fullmatch(run_id):
        fail('internal run ID error')
    intent = ARTIFACTS / (run_id + '.intent.json')
    write_new(intent, {'run_id': run_id, 'status': 'STARTED', 'training_eligible': False,
                      'threshold_tuning_eligible': False, 'gateway': ISOLATED,
                      'model': 'Candidate04 frozen shadow only', 'created_at': datetime.now(UTC).isoformat()})
    print('PILOT RUN:', run_id, flush=True)
    plan = [PlannedRequest(sequence=i+1, offset_seconds=i/RATE,
                           scenario='valid', amount_minor=4000+(i%100)) for i in range(COUNT)]
    started = datetime.now(UTC)
    observations = await asyncio.wait_for(execute_plan(plan, ISOLATED, run_id, 3304, 16), timeout=115)
    finished = datetime.now(UTC)
    req = proven_client_rows(observations, run_id)
    if len(req) != COUNT or any(x.get('expected_outcome') is not True or x.get('status_code') != 200 or x.get('transport_error') for x in req):
        fail('healthy isolated pilot requests were incomplete or unsuccessful; do not ingest')
    raw_events = raw_events_for(run_id, True)
    ev = visible_events(raw_events, run_id)
    validate_correlations(req, ev)
    window, canonical, scoped = slice_window(req, ev, started, finished, run_id)
    # Every event validated before the first POST. Do not post raw attributes.
    # Use the ENTIRE feature-relevant reduced log record for the mapping key,
    # never just timestamp/request_id (multiple events can share those).
    raw_index = {}
    for original in raw_events:
        reduced = visible_events([original], run_id)[0]
        key = json.dumps(reduced, sort_keys=True, separators=(',', ':'))
        raw_index.setdefault(key, []).append(original)
    ordered_raw = []
    for e in scoped:
        key = json.dumps(e, sort_keys=True, separators=(',', ':'))
        matches = raw_index.get(key)
        if not matches:
            fail('cannot uniquely map reduced feature event back to structured log')
        ordered_raw.append(matches.pop(0))
    # Collapse exact duplicate event fingerprints, mirroring V2 feature builder.
    seen, event_payloads = set(), []
    for raw in ordered_raw:
        key = json.dumps(visible_events([raw], run_id)[0], sort_keys=True, separators=(',', ':'))
        if key in seen:
            continue
        seen.add(key)
        event_payloads.append(event_wire(raw, run_id))
    # Take only requests starting no later than the precise feature end.
    request_payloads = [request_wire(x, run_id) for x in req if utc(x['started_at']) <= window.ended_at]
    if not event_payloads or not request_payloads:
        fail('empty validated run-scoped payloads')
    if any(utc(x['timestamp']) > window.ended_at for x in event_payloads):
        fail('post-window event slipped through ingestion guard')
    from scripts import faultweave_v2_isolated_db as iso
    from scripts.faultweave_v2_isolated_unknown_capture import validate_primary_stack
    iso.inspect_healthy()
    validate_primary_stack()
    for path in (iso.FIXTURE / 'fault-control/active_fault.json', ROOT / 'data/fault-control/active_fault.json'):
        if path.exists():
            fail('fault lease appeared during healthy pilot')
    # Ensure no other process filled the gateway during our 75-second collection.
    require_empty_buffer()
    print('PASS: genuine healthy isolated requests and correlated service logs verified')
    print('WINDOW:', window.started_at.isoformat(), 'to', window.ended_at.isoformat())
    ingest(event_payloads, request_payloads)
    snapshot = require_200(f'{ISOLATED}/api/v1/intelligence/live/features/60')
    strict_comparison(snapshot, canonical, window)
    result = require_200(f'{SHADOW}/api/v2/candidate04/infer',
                         {'window_seconds': 60, 'features': {x: canonical[x] for x in FEATURE_NAMES}})
    decision = result.get('decision')
    if not isinstance(decision, dict) or not isinstance(decision.get('prediction'), str):
        fail('Candidate04 returned an invalid inference decision')
    from scripts.faultweave_v2_candidate04_blind_eval import verify_frozen
    verify_frozen()
    record = {
        'run_id': run_id, 'status': 'VALIDATED_V2_LIVE_SHADOW_PILOT',
        'training_eligible': False, 'threshold_tuning_eligible': False,
        'gateway': ISOLATED, 'shadow': SHADOW, 'window_seconds': 60,
        'window_started_at': window.started_at.isoformat(), 'window_ended_at': window.ended_at.isoformat(),
        'request_count': snapshot['request_count'], 'event_count': snapshot['event_count'],
        'model_sha256': MODEL_SHA,
        'feature_schema_sha256': hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest(),
        'feature_vector_sha256': hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        'prediction': decision['prediction'],
        'source': 'new 75s isolated healthy research requests and correlated Docker logs',
    }
    write_new(ARTIFACTS / (run_id+'.result.json'), record)
    print('PASS: isolated 60s live gateway features match frozen 65-feature builder exactly')
    print('SHADOW PREDICTION:', decision['prediction'])
    print('RESEARCH-ONLY PILOT RECEIPT:', (ARTIFACTS / (run_id+'.result.json')).relative_to(ROOT))
    print('No fault injection, original gateway POST, model change, or training artifacts written.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--preview', action='store_true')
    group.add_argument('--preflight', action='store_true')
    group.add_argument('--capture', action='store_true')
    parser.add_argument('--acknowledge-isolated-healthy-traffic-and-telemetry-ingestion', action='store_true')
    args = parser.parse_args(argv)
    if args.preview:
        print('READ ONLY: Candidate04 isolated telemetry bridge, no active mode')
        print('Gateway:', ISOLATED, 'Shadow:', SHADOW)
        print('Healthy traffic:', COUNT, 'new transaction requests across', DURATION, 'seconds at', RATE, 'RPS')
        print('Both Docker stacks remain untouched; isolated gateway in-memory buffer POSTs only with explicit acknowledgment')
        print('No fault injection, DB reset, model or source writes; run will be research-only')
    elif args.preflight:
        preflight()
    elif args.capture:
        if not args.acknowledge_isolated_healthy_traffic_and_telemetry_ingestion:
            fail('explicit opt-in required before 113 real isolated transactions and telemetry POSTs')
        asyncio.run(capture())
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('TELEMETRY BRIDGE STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
