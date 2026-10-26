#!/usr/bin/env python3
"""FaultWeave v2 compact collection selection + guarded 60s training-data readiness.

This program does NOT execute fault experiments or approve training data. The user
must first collect independent, verified runs. Source plan is never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'data/datasets/v2/plan/phase19c2g-reachability-proposal.json'
OUTPUT_PLAN = ROOT / 'data/datasets/v2/plan/fasttrack-126-blocked.json'
EXPECTED_PARENT = '577b8e877466aae9df96c161ad911e0278cadaa50cf879dd89110d635c755cd9'
COUNTS = {'train': 8, 'validation': 2, 'test': 2}
NORMAL_COUNTS = {'train': 12, 'validation': 3, 'test': 3}
KNOWN_FAMILIES = frozenset({
    'AUTHENTICATION_FAILURE_BURST', 'CONNECTION_POOL_EXHAUSTION',
    'DATABASE_HIGH_LATENCY', 'DATABASE_LOCK_CONTENTION', 'DATABASE_UNAVAILABLE',
    'DOWNSTREAM_ERROR_BURST', 'DOWNSTREAM_TIMEOUT', 'HIGH_LOAD',
    'SERVICE_UNAVAILABLE',
})


class Stop(ValueError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda x: (_ for _ in ()).throw(Stop(x)))
    if not isinstance(value, dict):
        raise Stop(f'Expected JSON object: {path}')
    return value


def canonical_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def source_plan(root: Path) -> tuple[dict, str]:
    path = root / PLAN.relative_to(ROOT)
    obj = read_json(path)
    rows = obj.get('runs')
    if not isinstance(rows, list) or len(rows) != 240:
        raise Stop('Parent must contain exactly 240 planned runs')
    if obj.get('draft_sha256') != EXPECTED_PARENT:
        raise Stop('Parent provenance identifier changed')
    if len({row.get('run_id') for row in rows}) != 240:
        raise Stop('Parent run IDs are missing or duplicated')
    if any(row.get('execution_allowed') is not False or
           row.get('training_eligible') is not False or
           row.get('threshold_tuning_eligible') is not False or
           row.get('target_rps') is not None for row in rows):
        raise Stop('Parent includes unexpected approval')
    classes = {r.get('fault_id') for r in rows if r.get('scenario_type') == 'known_fault'}
    if classes != KNOWN_FAMILIES:
        raise Stop('Unexpected known fault family inventory')
    if sum(r.get('scenario_type') == 'normal' for r in rows) != 60:
        raise Stop('Parent normal inventory changed')
    return obj, sha256(path)


def select_diverse(rows: list[dict], limit: int) -> list[dict]:
    """Greedy deterministic coverage of intensity, rate regime, and target."""
    picked: list[dict] = []
    remaining = sorted(rows, key=lambda row: row['run_id'])
    intensity, rate, target = Counter(), Counter(), Counter()
    while len(picked) < limit:
        if not remaining:
            raise Stop('Not enough rows to satisfy compact split')
        def priority(row: dict) -> tuple:
            # Lower is better; balancing on multiple axes without touching splits.
            return (intensity[str(row.get('intensity'))] * 3 +
                    rate[str(row.get('rate_regime'))] * 2 +
                    target[str(row.get('target'))],
                    intensity[str(row.get('intensity'))],
                    rate[str(row.get('rate_regime'))],
                    row['run_id'])
        chosen = min(remaining, key=priority)
        remaining.remove(chosen)
        picked.append(chosen)
        intensity[str(chosen.get('intensity'))] += 1
        rate[str(chosen.get('rate_regime'))] += 1
        target[str(chosen.get('target'))] += 1
    return picked


def compact_plan(root: Path) -> dict:
    parent, digest = source_plan(root)
    by_group: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in parent['runs']:
        label = 'NORMAL' if row['scenario_type'] == 'normal' else row['fault_id']
        split = row['split']
        if label not in KNOWN_FAMILIES | {'NORMAL'} or split not in COUNTS:
            raise Stop('Unexpected parent row label or split')
        by_group[(label, split)].append(row)
    chosen = []
    for label in ['NORMAL', *sorted(KNOWN_FAMILIES)]:
        for split in ('train', 'validation', 'test'):
            n = (NORMAL_COUNTS if label == 'NORMAL' else COUNTS)[split]
            chosen.extend(select_diverse(by_group[(label, split)], n))
    if len(chosen) != 126 or len({r['run_id'] for r in chosen}) != 126:
        raise Stop('Compact selection integrity failed')
    return {
        'schema_version': 'faultweave-v2-fasttrack-draft.1',
        'purpose': 'Smaller sequential v2 research collection draft; NOT executable or training approved',
        'parent_plan_sha256': digest,
        'parent_draft_sha256': EXPECTED_PARENT,
        'selection': 'deterministic split-preserving multi-axis diversity',
        'normal_split_counts': NORMAL_COUNTS,
        'known_per_class_split_counts': COUNTS,
        'run_count': 126,
        'bulk_execution_allowed': False,
        'training_allowed': False,
        'caveats': [
            'All source proposal permissions remain false; target_rps remains null',
            'DATABASE_UNAVAILABLE requires isolated PostgreSQL; never stop the existing volume',
            'HIGH_LOAD must demonstrate an honest workload effect; 8 RPS is not proven overload',
            'AUTHENTICATION_FAILURE_BURST requires rate-specific proof',
            'Historical runs and diagnostic pilots are not admitted into model training',
            'Sealed unknown faults remain excluded from training and threshold tuning',
        ],
        'runs': chosen,
    }


def ensure_safe_path(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    resolved_root = root.resolve()
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root) or candidate.is_symlink():
        raise Stop('Evidence path must remain inside project root; no symlinks')
    if not resolved.is_file():
        raise Stop('Evidence path is not a regular file')
    return resolved


def verify_feature_rows(root: Path, feature_path: Path, receipt_path: Path,
                        *, include_unknown: bool = False) -> dict[str, Any]:
    """Fail-closed preliminary training gate; actual raw-audit receipts are mandatory.

    Receipt format is *not* generated by existing diagnostic collectors. The future
    independently verified training collector must issue one for each new run.
    """
    from datasets.final_features import FEATURE_NAMES
    features = ensure_safe_path(root, feature_path)
    receipts_file = ensure_safe_path(root, receipt_path)
    book = read_json(receipts_file)
    if (book.get('schema_version') != 'faultweave-v2-run-admission.1' or
        book.get('feature_file_sha256') != sha256(features) or
        book.get('independent_audit_required') is not True):
        raise Stop('Independent admission contract missing or feature file changed')
    receipt_rows = book.get('runs')
    if not isinstance(receipt_rows, list) or not receipt_rows:
        raise Stop('No admission receipts')
    indexes = {}
    for receipt in receipt_rows:
        run = receipt.get('run_id')
        if not isinstance(run, str) or not run or run in indexes:
            raise Stop('Missing/duplicate admission run ID')
        if receipt.get('training_admission') is not True or receipt.get('audit_verdict') != 'VALID_V2_TRAINING_RUN':
            raise Stop('Diagnostic, rejected or unapproved receipt present')
        for key in ('source_manifest', 'independent_audit'):
            evidence = receipt.get(key)
            if not isinstance(evidence, dict) or not isinstance(evidence.get('sha256'), str):
                raise Stop('Required evidence receipt not present')
            path = ensure_safe_path(root, Path(evidence.get('path', '')))
            if sha256(path) != evidence['sha256']:
                raise Stop('Independent evidence hash mismatch')
            doc = read_json(path)
            if doc.get('run_id') != run:
                raise Stop('Evidence run ID mismatch')
            if key == 'source_manifest':
                if (doc.get('training_eligible') is not True or
                    doc.get('split') != receipt.get('split') or
                    doc.get('scenario_type') != receipt.get('scenario_type') or
                    doc.get('label') != receipt.get('label') or
                    doc.get('window_seconds') != 60):
                    raise Stop('Source manifest is not an approved exact-window training capture')
            else:
                if doc.get('verdict') != 'VALID_V2_TRAINING_RUN' or doc.get('independent') is not True:
                    raise Stop('Independent audit did not approve this run')
        indexes[run] = receipt
    row_count, counts, by_run, unknowns = 0, Counter(), {}, 0
    with features.open('r', encoding='utf-8') as fd:
        for line in fd:
            if not line.strip():
                continue
            item = json.loads(line, parse_constant=lambda x: (_ for _ in ()).throw(Stop(x)))
            if not isinstance(item, dict):
                raise Stop('Feature line is not a JSON object')
            rid = item.get('run_id')
            receipt = indexes.get(rid)
            if receipt is None:
                raise Stop('Feature row without independent admission receipt')
            if rid in by_run:
                raise Stop('Only one exact 60-second feature row per independent run is allowed')
            by_run[rid] = (item.get('split'), item.get('label'))
            if (item.get('split') != receipt.get('split') or
                item.get('scenario_type') != receipt.get('scenario_type') or
                item.get('label') != receipt.get('label') or
                item.get('window_seconds') != 60 or
                item.get('training_eligible') is not True):
                raise Stop('Feature metadata and admitted source disagree')
            if item.get('split') not in COUNTS:
                raise Stop('Unknown feature split')
            if item.get('label') == 'NORMAL':
                if item['scenario_type'] != 'normal':
                    raise Stop('NORMAL feature labelled as fault')
            elif item.get('label') not in KNOWN_FAMILIES or item['scenario_type'] != 'known_fault':
                raise Stop('Unlisted known fault or unknown holdout in training file')
            values = item.get('features')
            if not isinstance(values, dict) or tuple(values) != FEATURE_NAMES:
                raise Stop('Exactly the frozen 65 feature names in order are required')
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values.values()):
                raise Stop('Nonfinite or nonnumeric feature value')
            if item.get('label') != 'NORMAL' and item.get('interval') != 'fault':
                raise Stop('Fault feature is outside a proven fault window')
            counts[(item['label'], item['split'])] += 1
            row_count += 1
    if set(by_run) != set(indexes):
        raise Stop('Missing feature rows for approved receipts')
    if row_count != 126:
        raise Stop(f'Exactly 126 independent admitted rows required, got {row_count}')
    for label in ['NORMAL', *sorted(KNOWN_FAMILIES)]:
        for split in ('train', 'validation', 'test'):
            required = (NORMAL_COUNTS if label == 'NORMAL' else COUNTS)[split]
            if counts[(label, split)] != required:
                raise Stop(f'{label}/{split}: expected {required}, found {counts[(label, split)]}')
    return {'status': 'READY_FOR_CANDIDATE_TRAINING', 'feature_rows': row_count,
            'independent_runs': len(by_run), 'source_feature_sha256': sha256(features),
            'normal': 18, 'known_classes': len(KNOWN_FAMILIES),
            'unknown_holdout_in_training': unknowns,
            'warning': 'Independent run approval required; this check is not a scientific accuracy guarantee'}


def candidate_training(root: Path, feature_path: Path, receipt_path: Path,
                       output: Path) -> dict:
    """Train a new, non-deployed candidate; only independently admitted 60s rows."""
    ready = verify_feature_rows(root, feature_path, receipt_path)
    from datasets.final_features import FEATURE_NAMES
    import numpy as np
    import joblib
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import f1_score, accuracy_score, classification_report, confusion_matrix
    from xgboost import XGBClassifier

    rows = []
    with ensure_safe_path(root, feature_path).open(encoding='utf-8') as fd:
        for line in fd:
            if line.strip():
                rows.append(json.loads(line))
    train = [r for r in rows if r['split'] == 'train']
    validation = [r for r in rows if r['split'] == 'validation']
    test = [r for r in rows if r['split'] == 'test']
    # All input feature dimensions and labels are validated above.
    def vectors(items):
        return np.array([[r['features'][name] for name in FEATURE_NAMES] for r in items], dtype=np.float64)
    normal_train = [r for r in train if r['label'] == 'NORMAL']
    fault_train = [r for r in train if r['label'] != 'NORMAL']
    known = sorted(KNOWN_FAMILIES)
    normal_scaler = StandardScaler().fit(vectors(normal_train))
    detector = IsolationForest(n_estimators=200, random_state=20261902, n_jobs=-1)
    detector.fit(normal_scaler.transform(vectors(normal_train)))
    def scores(items):
        return -detector.decision_function(normal_scaler.transform(vectors(items)))
    val_scores = scores(validation)
    labels_val = np.array([r['label'] != 'NORMAL' for r in validation])
    # Score threshold is tuned using validation only, never held-out test rows.
    thresholds = np.unique(val_scores)
    anomaly_threshold = float(max((float(f1_score(labels_val, val_scores >= t, zero_division=0)),
                                   -float(t), float(t)) for t in thresholds)[2])
    clf = XGBClassifier(n_estimators=120, max_depth=3, learning_rate=0.08,
                        objective='multi:softprob', num_class=9, random_state=20261902,
                        n_jobs=2, eval_metric='mlogloss')
    clf.fit(vectors(fault_train), np.array([known.index(r['label']) for r in fault_train]))
    known_scaler = StandardScaler().fit(vectors(fault_train))
    known_train_x = known_scaler.transform(vectors(fault_train))
    centroids = np.stack([known_train_x[[i for i, r in enumerate(fault_train)
                                         if r['label'] == cls]].mean(axis=0)
                          for cls in known])
    known_val = [r for r in validation if r['label'] != 'NORMAL']
    probs = clf.predict_proba(vectors(known_val))
    conf_floor = float(np.quantile(np.max(probs, axis=1), 0.10))
    predicted_ids = np.argmax(probs, axis=1)
    distances = np.linalg.norm(known_scaler.transform(vectors(known_val)) -
                               centroids[predicted_ids], axis=1)
    distance_ceiling = float(np.quantile(distances, 0.95))
    def decide(items):
        raw = clf.predict_proba(vectors(items))
        indices = np.argmax(raw, axis=1)
        confs = np.max(raw, axis=1)
        dists = np.linalg.norm(known_scaler.transform(vectors(items)) -
                               centroids[indices], axis=1)
        anomalies = scores(items) >= anomaly_threshold
        return [ ('NORMAL' if not abnormal else
                  'UNKNOWN ABNORMAL PATTERN' if conf < conf_floor or distance > distance_ceiling
                  else known[idx])
                 for abnormal, idx, conf, distance in zip(anomalies, indices, confs, dists, strict=True)]
    test_predictions = decide(test)
    truth = [r['label'] for r in test]
    known_test = [r for r in test if r['label'] != 'NORMAL']
    known_predict = [pred for r, pred in zip(test, test_predictions, strict=True) if r['label'] != 'NORMAL']
    anomaly_true = [r['label'] != 'NORMAL' for r in test]
    anomaly_pred = [scores([r])[0] >= anomaly_threshold for r in test]
    report = {
        'status': 'PRELIMINARY_V2_CANDIDATE_NOT_DEPLOYED',
        'training_data': ready,
        'training_runs': len(train), 'validation_runs': len(validation), 'untouched_test_runs': len(test),
        'model_names': ['IsolationForest', 'XGBoost'],
        'validation_only_thresholds': {'anomaly_score': anomaly_threshold,
                                       'known_confidence': conf_floor,
                                       'known_support_distance': distance_ceiling},
        'test_metrics': {
            'normal_vs_abnormal_f1': float(f1_score(anomaly_true, anomaly_pred, zero_division=0)),
            'known_fault_macro_f1': float(f1_score([r['label'] for r in known_test], known_predict,
                                                 labels=known, average='macro', zero_division=0)),
            'all_labels_accuracy': float(accuracy_score(truth, test_predictions)),
            'classification_report': classification_report(truth, test_predictions,
                                                            labels=['NORMAL', *known, 'UNKNOWN ABNORMAL PATTERN'],
                                                            output_dict=True, zero_division=0),
            'confusion_labels': ['NORMAL', *known, 'UNKNOWN ABNORMAL PATTERN'],
            'confusion_matrix': confusion_matrix(truth, test_predictions,
                                                 labels=['NORMAL', *known, 'UNKNOWN ABNORMAL PATTERN']).tolist(),
        },
        'sealed_unknown_families_used_for_training_or_calibration': 0,
        'sealed_unknown_evaluation': 'NOT_PERFORMED; must use fresh reserved holdout separately',
        'activation': 'NOT_AUTHORIZED; independently evaluate before API/dashboard switch',
    }
    destination = output if output.is_absolute() else root / output
    base = root / 'data/datasets/v2/models'
    # Candidate models never touch the frozen v1 model root.
    try:
        if (base.is_symlink() or destination.is_symlink() or
            destination.resolve(strict=False).parent != base.resolve(strict=False) or
            destination.exists() or
            not destination.resolve(strict=False).is_relative_to(root.resolve())):
            raise Stop('Candidate must be a new immediate child of data/datasets/v2/models')
        for part in (root / 'data', root / 'data/datasets', root / 'data/datasets/v2'):
            if part.is_symlink() or not part.is_dir():
                raise Stop('Expected existing dataset parent is missing or symlinked')
        if base.exists() and not base.is_dir():
            raise Stop('Invalid v2 model output parent')
        base.mkdir(exist_ok=True)
    except OSError as exc:
        raise Stop(f'Cannot create candidate directory safely: {exc}') from exc
    destination.mkdir(exist_ok=False)
    joblib.dump({'model': detector, 'scaler': normal_scaler,
                 'feature_names': list(FEATURE_NAMES), 'threshold': anomaly_threshold},
                destination / 'isolation_forest.joblib')
    joblib.dump({'model': clf, 'classes': known, 'scaler': known_scaler,
                 'centroids': centroids, 'confidence_floor': conf_floor,
                 'distance_ceiling': distance_ceiling, 'feature_names': list(FEATURE_NAMES)},
                destination / 'xgboost_open_set.joblib')
    (destination / 'evaluation.json').write_bytes(canonical_bytes(report))
    return {'candidate_path': str(destination), 'status': report['status'],
            'test_accuracy': report['test_metrics']['all_labels_accuracy'],
            'sealed_unknown_evaluated': False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--preview', action='store_true', help='read-only deterministic 126-run selection')
    action.add_argument('--write-draft', action='store_true', help='create-only immutable blocked compact draft')
    action.add_argument('--check-training', action='store_true', help='verify independent admitted feature receipts (never train)')
    action.add_argument('--train-candidate', action='store_true', help='train and evaluate a separate, non-deployed Model v2 candidate')
    parser.add_argument('--features', type=Path)
    parser.add_argument('--receipts', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--acknowledge-training-candidate', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.train_candidate:
            if (args.features is None or args.receipts is None or args.output is None or
                not args.acknowledge_training_candidate):
                raise Stop('Candidate training requires --features, --receipts, --output and explicit acknowledgement')
            print(json.dumps(candidate_training(ROOT, args.features, args.receipts, args.output), indent=2))
            return 0
        if args.check_training:
            if args.features is None or args.receipts is None:
                raise Stop('--check-training requires --features and --receipts')
            result = verify_feature_rows(ROOT, args.features, args.receipts)
            print(json.dumps(result, indent=2))
            return 0
        if (args.features is not None or args.receipts is not None or args.output is not None or
            args.acknowledge_training_candidate):
            raise Stop('Feature options only apply to --check-training')
        plan = compact_plan(ROOT)
        print('FASTTRACK MODEL V2: 126 blocked independent planned runs (18 normal + 108 faults)')
        print('Per known class: 8 train / 2 validation / 2 test; normal: 12 / 3 / 3')
        print('9 known fault families. Unknown holdout never used for training.')
        print('NO DIAGNOSTIC PROMOTION; NO BULK EXECUTION; NO MODEL WRITES')
        if args.write_draft:
            output = ROOT / OUTPUT_PLAN.relative_to(ROOT)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('xb') as fd:
                fd.write(canonical_bytes(plan))
            print('CREATE-ONLY BLOCKED DRAFT:', output)
        else:
            print('READ ONLY: no files written')
        return 0
    except (Stop, OSError, TypeError, KeyError, ValueError, ImportError) as exc:
        print('FASTTRACK STOP:', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
