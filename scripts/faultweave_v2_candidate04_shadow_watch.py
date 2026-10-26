"""Research-only, loopback-only rolling snapshot watcher for frozen Candidate 04.

A separate, trusted source MUST ingest real correlated events AND independently
observed client requests into the isolated gateway first. This watcher NEVER
posts telemetry, generates transactions, injects faults or changes Docker.

--preview: no network/files
--preflight: read-only HTTP contract GETs, local model hash verification
--check: read-only snapshot quality check, no inference or writes
--once/--watch: explicit opt-in required; POST ONLY to shadow /infer;
                create-only research audit receipts; never affects incidents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPHandler, HTTPRedirectHandler, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = 'http://127.0.0.1:28110'
SHADOW = 'http://127.0.0.1:28190'
MODEL_SHA256 = '7bbf6c2b0ba57d40a45d1ac633c3b50ecfe9ed1b2ea6354d460a32fd120da0be'
OUT = ROOT / '.runtime' / 'v2-shadow-watch'
MAX_AGE_SECONDS = 20.0
MIN_REQUESTS = 70
MIN_EVENTS = 70


class WatchError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise WatchError('STOP: ' + message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(url: str, payload: dict | None = None) -> tuple[int, dict]:
    allowed = (GATEWAY, SHADOW)
    if not any(url == prefix or url.startswith(prefix + '/') for prefix in allowed):
        fail('unapproved HTTP destination; only isolated gateway and shadow model permitted')
    if payload is not None and url != SHADOW + '/api/v2/candidate04/infer':
        fail('POST permitted only to the frozen localhost shadow inference endpoint')
    body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    req = Request(url, data=body, method='GET' if body is None else 'POST',
                  headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    opener = build_opener(ProxyHandler({}), HTTPHandler(), NoRedirect())
    try:
        with opener.open(req, timeout=15) as res:
            code, raw = res.status, res.read(1024 * 1024 + 1)
    except HTTPError as exc:
        code, raw = exc.code, exc.read(1024 * 1024 + 1)
    except (URLError, OSError) as exc:
        fail(f'localhost HTTP unavailable: {type(exc).__name__}: {exc}')
    if len(raw) > 1024 * 1024:
        fail('oversize local API response')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        fail(f'non-JSON response HTTP {code}: {exc}')
    if not isinstance(data, dict):
        fail('local API response is not a JSON object')
    return code, data


def require_ok(url: str, payload: dict | None = None) -> dict:
    code, data = api(url, payload)
    if code != 200:
        fail(f'HTTP {code} from {url}: {str(data.get("detail", ""))[:160]}')
    return data


def frozen_names() -> tuple[str, ...]:
    from datasets.final_features import FEATURE_NAMES
    result = tuple(FEATURE_NAMES)
    if len(result) != 65 or len(set(result)) != 65:
        fail('local Candidate 04 65-feature schema invalid')
    return result


def check_contract() -> tuple[str, ...]:
    from scripts.faultweave_v2_candidate04_blind_eval import verify_frozen
    verify_frozen()
    ready = require_ok(SHADOW + '/api/v2/candidate04/ready')
    if (ready.get('status') != 'READY_FOR_RESEARCH_INFERENCE'
            or ready.get('model_sha256', '').lower() != MODEL_SHA256
            or ready.get('feature_count') != 65
            or ready.get('deployment_status') != 'RESEARCH_ONLY_NOT_DEPLOYED'):
        fail('shadow API is not the frozen Candidate 04 research service')
    schema = require_ok(SHADOW + '/api/v2/candidate04/schema')
    names = frozen_names()
    if schema.get('feature_count') != 65 or schema.get('feature_names') != list(names):
        fail('shadow/local Candidate 04 65-feature order mismatch')
    return names


def parse_utc(s: object) -> datetime:
    if not isinstance(s, str):
        fail('window timestamp must be a string')
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        fail('invalid window timestamp')
    if dt.tzinfo is None:
        fail('timezone missing in window timestamp')
    return dt.astimezone(timezone.utc)


def snapshot_gate(snapshot: dict, names: tuple[str, ...], *, now: datetime | None = None) -> dict:
    """Only recently completed, covered, client-observed, numeric 60s windows."""
    if snapshot.get('window_seconds') != 60:
        fail('expected exact 60-second snapshot')
    if snapshot.get('request_observation_source') != 'client_observed':
        fail('gateway-derived requests are not an independently observed client source')
    start = parse_utc(snapshot.get('window_started_at'))
    end = parse_utc(snapshot.get('window_ended_at'))
    if abs((end-start).total_seconds() - 60) > .001:
        fail('snapshot timestamps not exactly 60 seconds')
    current = now or datetime.now(timezone.utc)
    age = (current-end).total_seconds()
    if age < -2:
        fail('telemetry watermark is in the future; clock skew')
    if age > MAX_AGE_SECONDS:
        return {'kind': 'SKIP_STALE', 'age_seconds': round(age, 3), 'ended_at': end.isoformat()}
    coverage = snapshot.get('coverage_seconds')
    if type(coverage) not in (int, float) or not math.isfinite(coverage) or coverage < 60:
        fail('incomplete 60-second service-event coverage')
    request_count, event_count = snapshot.get('request_count'), snapshot.get('event_count')
    if type(request_count) is not int or request_count < MIN_REQUESTS:
        fail(f'insufficient client request evidence (<{MIN_REQUESTS})')
    if type(event_count) is not int or event_count < MIN_EVENTS:
        fail(f'insufficient service event evidence (<{MIN_EVENTS})')
    features = snapshot.get('features')
    if not isinstance(features, dict) or set(features) != set(names):
        fail('gateway features differ from frozen 65-feature schema')
    if any(type(features[name]) not in (int, float) or not math.isfinite(features[name]) for name in names):
        fail('non-finite/non-numeric/bool feature detected')
    vector = {name: features[name] for name in names}
    return {
        'kind': 'READY', 'age_seconds': round(age, 3),
        'started_at': start.isoformat(), 'ended_at': end.isoformat(),
        'request_count': request_count, 'event_count': event_count,
        'features': vector,
    }


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def receipt_path(end_iso: str) -> Path:
    # Deterministically name each window without relying on gateway-provided run_id.
    key = hashlib.sha256(end_iso.encode()).hexdigest()[:24]
    return OUT / (f'{key}.prediction.json')


def persist(path: Path, result: dict) -> bool:
    """Create once. Same-window but different contents fail closed."""
    if path.is_symlink():
        fail('prediction receipt destination is symlink')
    if path.exists():
        existing = json.loads(path.read_text(encoding='utf-8'))
        for key in ('window_ended_at', 'feature_vector_sha256', 'prediction', 'model_sha256'):
            if existing.get(key) != result.get(key):
                fail('previously processed window has conflicting inference; preserving original')
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        with os.fdopen(os.open(path, flags, 0o600), 'w', encoding='utf-8') as handle:
            json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        fail('another process created the same window receipt; do not retry')
    return True


def recent_nonoverlapping_window(end_iso: str) -> bool:
    """Research watcher may infer at most once per 60-second evidence interval."""
    end = parse_utc(end_iso)
    if not OUT.exists():
        return True
    if not OUT.is_dir() or OUT.is_symlink():
        fail('research receipt directory is unsafe')
    for path in OUT.glob('*.prediction.json'):
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
            fail('unsafe prior research receipt')
        row = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(row, dict) or row.get('model_sha256') != MODEL_SHA256
                or not isinstance(row.get('window_ended_at'), str)):
            fail('unverifiable prior research receipt')
        earlier = parse_utc(row['window_ended_at'])
        if path.name != receipt_path(earlier.isoformat()).name:
            fail('prior research receipt filename mismatch')
        separation = (end - earlier).total_seconds()
        if separation < 0:
            fail('prior research receipt is newer than gateway window')
        if separation < 60:
            return False
    return True


def one_poll(names: tuple[str, ...], *, infer: bool, now: datetime | None = None) -> str:
    code, snapshot = api(GATEWAY + '/api/v1/intelligence/live/features/60')
    if code == 409:
        print('WAIT: gateway 60-second telemetry window unavailable: ' + str(snapshot.get('detail',''))[:160])
        return 'WAIT'
    if code != 200:
        fail(f'gateway feature endpoint HTTP {code}')
    checked = snapshot_gate(snapshot, names, now=now)
    if checked['kind'] == 'SKIP_STALE':
        print(f'SKIP: last 60s window is stale ({checked["age_seconds"]}s old); no inference')
        return 'STALE'
    path = receipt_path(checked['ended_at'])
    if path.exists():
        # A new process may encounter existing receipt; do not re-infer it.
        original = json.loads(path.read_text(encoding='utf-8'))
        if (original.get('feature_vector_sha256') != fingerprint(checked['features'])
                or original.get('window_ended_at') != checked['ended_at']
                or original.get('model_sha256') != MODEL_SHA256):
            fail('existing window receipt differs from current gateway features')
        print('SKIP: window already committed; no duplicate inference')
        return 'DUPLICATE'
    if not infer:
        print(f'READ ONLY: FRESH 60s client-observed window; requests={checked["request_count"]}, events={checked["event_count"]}; no inference')
        return 'READY'
    if not recent_nonoverlapping_window(checked['ended_at']):
        print('SKIP: latest 60s research window overlaps an earlier inference receipt')
        return 'OVERLAP'
    # Guard the remote frozen model contract before every prediction.
    status = require_ok(SHADOW + '/api/v2/candidate04/ready')
    if (status.get('model_sha256', '').lower() != MODEL_SHA256 or
            status.get('deployment_status') != 'RESEARCH_ONLY_NOT_DEPLOYED'):
        fail('shadow model identity changed during watch')
    answer = require_ok(SHADOW + '/api/v2/candidate04/infer',
                        {'window_seconds': 60, 'features': checked['features']})
    decision = answer.get('decision')
    if not isinstance(decision, dict) or not isinstance(decision.get('prediction'), str):
        fail('invalid shadow inference response')
    receipt = {
        'status': 'RESEARCH_ONLY_SHADOW_MONITOR', 'deployment_status': 'RESEARCH_ONLY_NOT_DEPLOYED',
        'training_eligible': False, 'threshold_tuning_eligible': False,
        'gateway': GATEWAY, 'shadow': SHADOW, 'model_sha256': MODEL_SHA256,
        'window_seconds': 60, 'window_started_at': checked['started_at'],
        'window_ended_at': checked['ended_at'], 'age_seconds_at_inference': checked['age_seconds'],
        'request_count': checked['request_count'], 'event_count': checked['event_count'],
        'feature_vector_sha256': fingerprint(checked['features']),
        'prediction': decision['prediction'],
        'provenance_limitation': 'Snapshot is client_observed but contains no per-run identity or independently replayed raw evidence; research-only',
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    created = persist(path, receipt)
    if created:
        print(f'RESEARCH-ONLY SHADOW PREDICTION: {decision["prediction"]}')
        print('CREATE-ONLY RECEIPT:', path)
        print('RECEIPT SHA256:', hashlib.sha256(path.read_bytes()).hexdigest())
    return 'PREDICTED' if created else 'DUPLICATE'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preview', action='store_true')
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--once', action='store_true')
    modes.add_argument('--watch', action='store_true')
    parser.add_argument('--poll-seconds', type=int, default=15)
    parser.add_argument('--max-polls', type=int, default=0)
    parser.add_argument('--acknowledge-research-only-shadow-inference', action='store_true')
    a = parser.parse_args(argv)
    if a.preview:
        print('READ ONLY: no network, Docker, transactions, telemetry POST, or inference')
        print('Targets:', GATEWAY, SHADOW)
        print('Read exact 60s window; reject stale (>20s) or gateway-derived client data')
        print('Writes ONLY create-new .runtime/v2-shadow-watch receipts in active inference modes')
        print('IMPORTANT: this is an inference watcher, NOT a continuous telemetry collector')
        return 0
    if a.once or a.watch:
        if not a.acknowledge_research_only_shadow_inference:
            fail('explicit research-only shadow-inference acknowledgement required')
    if not (5 <= a.poll_seconds <= 300):
        fail('poll interval must be 5..300 seconds')
    if a.max_polls < 0 or a.max_polls > 10000:
        fail('max polls must be 0 (unlimited) or 1..10000')
    names = check_contract()
    if a.preflight:
        print('PASS: frozen model and exact 65-feature contract; NO traffic, inference, or writes')
        return 0
    if a.check:
        one_poll(names, infer=False)
        return 0
    if a.once:
        one_poll(names, infer=True)
        return 0
    print(f'WATCH STARTED: poll every {a.poll_seconds}s; zero means unlimited max polls')
    count = 0
    while True:
        one_poll(names, infer=True)
        count += 1
        if a.max_polls and count >= a.max_polls:
            break
        time.sleep(a.poll_seconds)
    print('WATCH FINISHED:', count, 'poll(s)')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (WatchError, OSError, ImportError, ValueError, KeyError) as exc:
        print('SHADOW WATCH STOP:', exc, file=sys.stderr)
        raise SystemExit(2)
