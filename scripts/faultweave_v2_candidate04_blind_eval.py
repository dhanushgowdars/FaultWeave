"""FaultWeave: offline, create-only blind evaluation of a frozen V2 Candidate 04.

This module DOES NOT collect traffic, arm a fault, talk to Docker, or start the
legacy final_executor. It consumes ONLY an already completed evaluation-only
manifest and its captured request, event and interval evidence.

From the FaultWeave repository root:
  python -m scripts.faultweave_v2_candidate04_blind_eval --preflight
  python -m scripts.faultweave_v2_candidate04_blind_eval --predict-existing \
      --manifest data/datasets/final/manifests/<RUN_ID>.json
  python -m scripts.faultweave_v2_candidate04_blind_eval --reveal \
      --receipt data/datasets/v2/evaluation-only/candidate04/<RUN_ID>.prediction.json

Predictions are committed to a create-only file *before* --reveal can read the
hidden fault ID. Source run must have a successful recovery and hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIR = ROOT / 'data/datasets/v2/models/v2-verified126-candidate-04'
MODEL_PATH = CANDIDATE_DIR / 'candidate04_bundle.joblib'
MODEL_EVAL_PATH = CANDIDATE_DIR / 'evaluation.json'
TRAIN_FEATURES = ROOT / 'data/datasets/v2/assembled/verified-126-v1/features.jsonl'
SOURCE_CODE = ROOT / 'scripts/faultweave_v2_candidate04.py'
MANIFEST_ROOT = ROOT / 'data/datasets/final/manifests'
CAPTURE_ROOT = ROOT / 'data/datasets/final'
RESULTS_ROOT = ROOT / 'data/datasets/v2/evaluation-only/candidate04'

EXPECTED_HASHES = {
    SOURCE_CODE: '02638e2dd910c89af8917ead91907da9a28667f78fbd62681ffdbdf652ea0c0b',
    MODEL_PATH: '7bbf6c2b0ba57d40a45d1ac633c3b50ecfe9ed1b2ea6354d460a32fd120da0be',
    MODEL_EVAL_PATH: 'b2f6ed3fc05d2abcd2e8402fddb7b819571b8ccb7fda6f372067c61922a618dc',
    TRAIN_FEATURES: 'dfa6d33c909863db76261184f4e8bad6b7fe23597e67994cee055e397a90865c',
}
RUN_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,75}$')


def stop(message: str) -> None:
    raise RuntimeError('STOP: ' + message)


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as f:
        while piece := f.read(1024 * 1024):
            hasher.update(piece)
    return hasher.hexdigest()


def require_regular(path: Path, allowed_dir: Path | None = None) -> Path:
    """Forbid symlinked data and paths escaping the permitted subtree."""
    absolute = path if path.is_absolute() else ROOT / path
    if not absolute.is_file() or absolute.is_symlink():
        stop(f'missing file or symlink: {absolute}')
    if allowed_dir is not None:
        if not absolute.resolve().is_relative_to(allowed_dir.resolve()):
            stop(f'path is outside approved subtree: {absolute}')
        for parent in (absolute, *absolute.parents):
            if parent == allowed_dir.parent:
                break
            if parent.is_symlink():
                stop(f'symlinked path component: {parent}')
    return absolute


def verify_frozen() -> None:
    for path, expected in EXPECTED_HASHES.items():
        require_regular(path, ROOT)
        actual = sha256(path)
        if actual != expected:
            stop(f'frozen artifact changed: {path.relative_to(ROOT)}; expected {expected} got {actual}')
    print('PASS: frozen Candidate 04 code/model/report and original V2 feature dataset match recorded SHA256.')


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        stop(f'expected object in {path}')
    return obj


def utc_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        stop('naive timestamps are forbidden')
    return dt.astimezone(timezone.utc)


def validate_artifact(manifest: dict[str, Any], key: str) -> Path:
    artifact = manifest.get(key)
    if not isinstance(artifact, dict):
        stop(f'run manifest missing {key} artifact')
    rel = artifact.get('path')
    if not isinstance(rel, str):
        stop(f'{key}: invalid path')
    path = require_regular(Path(rel), CAPTURE_ROOT)
    expected = artifact.get('sha256')
    if not isinstance(expected, str) or sha256(path) != expected.lower():
        stop(f'{key} artifact hash verification failed')
    return path


def checked_capture(manifest_path: Path):
    path = require_regular(manifest_path, MANIFEST_ROOT)
    manifest = load_json(path)
    rid = manifest.get('run_id')
    if not isinstance(rid, str) or not RUN_ID_RE.fullmatch(rid) or path.name != f'{rid}.json':
        stop('unrecognized or mismatched run_id')
    if (manifest.get('split') != 'evaluation_only'
        or manifest.get('training_eligible') is not False
        or manifest.get('threshold_tuning_eligible') is not False
        or manifest.get('recovery_verified') is not True):
        stop('manifest must be an independently completed evaluation-only run with verified recovery')
    if 'unknown' not in str(manifest.get('scenario_type', '')).lower():
        stop('not a sealed-unknown evaluation run')
    request_path = validate_artifact(manifest, 'requests')
    event_path = validate_artifact(manifest, 'events')
    truth_path = validate_artifact(manifest, 'ground_truth')
    return path, manifest, rid, request_path, event_path, truth_path


def lines_json(path: Path):
    with path.open(encoding='utf-8') as f:
        for index, line in enumerate(f, 1):
            if line.strip():
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    stop(f'invalid JSONL row {index} in {path}')
                yield obj


def interval_window_only(truth_path: Path, rid: str) -> tuple[datetime, datetime]:
    """Use ground-truth file strictly for *timing*, never model input/labels."""
    truth = load_json(truth_path)
    if truth.get('run_id') != rid or truth.get('split') != 'evaluation_only':
        stop('ground truth run ID or partition differs from manifest')
    if truth.get('training_eligible') is not False or truth.get('threshold_tuning_eligible') is not False:
        stop('ground truth is not evaluation-only')
    intervals = [item for item in truth.get('intervals', []) if item.get('name') == 'fault']
    if len(intervals) != 1:
        stop('requires exactly one fault interval')
    start = utc_timestamp(intervals[0]['started_at'])
    observed_end = utc_timestamp(intervals[0]['ended_at'])
    end = start + timedelta(seconds=60)
    if observed_end < end:
        stop('fault interval lacks an EXACT full 60-second window; no padding or label substitution allowed')
    return start, end


def select_observations(path: Path, key: str, start: datetime, end: datetime, rid: str):
    selected = []
    for item in lines_json(path):
        if item.get('run_id') != rid:
            stop('captured raw telemetry includes a different run_id')
        t = utc_timestamp(str(item[key]))
        if start <= t < end:
            selected.append(item)
    if not selected:
        stop(f'no {key} telemetry within the exact 60-second fault interval')
    return selected


def only_numeric_features(requests, events) -> dict[str, float]:
    """Reuse FaultWeave's *exact* frozen feature calculations."""
    from datasets.final_features import FEATURE_NAMES, _request_features, _event_features
    features = _request_features(requests, 60.0)
    features.update(_event_features(events, 60.0))
    if tuple(features.keys()) != FEATURE_NAMES or len(FEATURE_NAMES) != 65:
        stop('feature schema/key ORDER diverges from training')
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in features.values()):
        stop('features contain nonfinite or non-numeric entries')
    return features


def immutable_json(path: Path, content: dict[str, Any]) -> None:
    """Atomic create-only receipt; existing files cannot be overwritten."""
    if path.exists() or path.is_symlink():
        stop(f'output already exists: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(content, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def predict_existing(manifest_path: Path) -> None:
    verify_frozen()
    path, manifest, rid, req_path, event_path, truth_path = checked_capture(manifest_path)
    receipt_path = RESULTS_ROOT / f'{rid}.prediction.json'
    if receipt_path.exists() or receipt_path.is_symlink():
        stop('prediction already recorded; do not retry or overwrite')
    start, end = interval_window_only(truth_path, rid)
    requests = select_observations(req_path, 'started_at', start, end, rid)
    events = select_observations(event_path, 'timestamp', start, end, rid)
    ids = {r.get('request_id') for r in requests}
    if None in ids or any(i not in {e.get('request_id') for e in events} for i in ids):
        stop('request/event correlation incomplete in feature window')
    features = only_numeric_features(requests, events)

    # Locked-down local trusted bundle only. Never load artifacts from a capture.
    import joblib
    from datasets.final_features import FEATURE_NAMES
    from scripts.faultweave_v2_candidate04 import predict as model_predict
    bundle = joblib.load(MODEL_PATH)
    if bundle.get('feature_names') != list(FEATURE_NAMES):
        stop('frozen candidate bundle feature ordering changed')
    result = model_predict(bundle['model'], bundle['isolation'], [{'features': features}])[0]
    permitted = ('prediction', 'reason', 'binary_fault', 'iso_anomaly',
                 'nearest_predicted_class', 'known_distance', 'known_radius',
                 'normal_distance', 'normal_radius', 'isolation_score')
    diagnostics = {key: result[key] for key in permitted}
    # No ground-truth label, fault_id, target, intensity, or scenario metadata.
    receipt = {
        'schema_version': 'faultweave-candidate04-evaluation-prediction.1',
        'run_id': rid,
        'model_sha256': sha256(MODEL_PATH),
        'manifest_path': str(path.relative_to(ROOT)).replace('\\', '/'),
        'manifest_sha256': sha256(path),
        'requests_sha256': sha256(req_path),
        'events_sha256': sha256(event_path),
        'ground_truth_sha256': sha256(truth_path),
        'window_started_at': start.isoformat(),
        'window_ended_at': end.isoformat(),
        'feature_count': len(features),
        'feature_names': list(FEATURE_NAMES),
        'features': features,
        'request_count': len(requests),
        'event_count': len(events),
        'decision': diagnostics,
        'ground_truth_revealed': False,
        'model_updated': False,
    }
    immutable_json(receipt_path, receipt)
    print('PREDICTION COMMITTED BEFORE REVEAL:', receipt_path.relative_to(ROOT))
    print('PREDICTION:', diagnostics['prediction'])
    print('SHA256 RECEIPT:', sha256(receipt_path))
    print('Do not modify the prediction receipt. To score, run --reveal with this receipt path.')


def reveal(receipt_path: Path) -> None:
    verify_frozen()
    path = require_regular(receipt_path, RESULTS_ROOT)
    if not path.name.endswith('.prediction.json'):
        stop('expected a prediction receipt')
    receipt = load_json(path)
    rid = receipt.get('run_id')
    if not isinstance(rid, str) or not RUN_ID_RE.fullmatch(rid) or path.name != f'{rid}.prediction.json':
        stop('run and receipt filename mismatch')
    if receipt.get('schema_version') != 'faultweave-candidate04-evaluation-prediction.1':
        stop('unsupported prediction schema')
    if receipt.get('model_sha256') != sha256(MODEL_PATH):
        stop('model changed after prediction')
    manifest_path = require_regular(Path(receipt['manifest_path']), MANIFEST_ROOT)
    if sha256(manifest_path) != receipt['manifest_sha256']:
        stop('capture manifest changed since prediction')
    _, manifest, check_rid, req_path, event_path, truth_path = checked_capture(manifest_path)
    if check_rid != rid or sha256(req_path) != receipt['requests_sha256'] or sha256(event_path) != receipt['events_sha256']:
        stop('raw evidence changed since prediction')
    if sha256(truth_path) != receipt['ground_truth_sha256']:
        stop('hidden truth changed since prediction')
    result_path = RESULTS_ROOT / f'{rid}.score.json'
    if result_path.exists() or result_path.is_symlink():
        stop('score already exists; do not overwrite')
    # Ground truth read for interpretation ONLY at the explicit reveal step.
    truth = load_json(truth_path)
    actual = truth.get('fault_id')
    if not isinstance(actual, str) or not actual:
        stop('missing sealed-unknown ground truth')
    # Validate against the frozen registry only after prediction is committed.
    from experiments.sealed_unknowns.catalog import SealedUnknownFamily
    if actual not in {x.value for x in SealedUnknownFamily}:
        stop('ground truth is not a frozen sealed-unknown family')
    predicted = receipt['decision']['prediction']
    success = predicted == 'UNKNOWN ABNORMAL PATTERN'
    record = {
        'schema_version': 'faultweave-candidate04-evaluation-reveal.1',
        'run_id': rid,
        'prediction_sha256': sha256(path),
        'model_sha256': sha256(MODEL_PATH),
        'actual_fault_family': actual,
        'prediction': predicted,
        'unknown_rejected': success,
        'recovery_verified': bool(manifest['recovery_verified']),
        'frozen_candidate_unchanged': True,
        'caveat': 'One 60s evaluation window does not establish generalization; do not tune against reserved unknowns.',
    }
    immutable_json(result_path, record)
    print('REVEAL COMMITTED:', result_path.relative_to(ROOT))
    print('UNKNOWN REJECTION:', 'PASS' if success else 'FAIL')
    print('ACTUAL FAMILY:', actual)
    print('PREDICTED:', predicted)
    print('PREDICTION HASH:', sha256(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--preflight', action='store_true', help='verify frozen artifact hashes; NO traffic')
    operation.add_argument('--predict-existing', action='store_true', help='run frozen inference on completed evaluation-only manifest')
    operation.add_argument('--reveal', action='store_true', help='score a previously committed prediction receipt')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if args.preflight:
        if args.manifest or args.receipt: parser.error('--preflight accepts no manifest/receipt')
        verify_frozen()
    elif args.predict_existing:
        if not args.manifest or args.receipt: parser.error('--predict-existing requires --manifest only')
        predict_existing(args.manifest)
    else:
        if not args.receipt or args.manifest: parser.error('--reveal requires --receipt only')
        reveal(args.receipt)

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as err:
        print(err)
        raise SystemExit(1)
