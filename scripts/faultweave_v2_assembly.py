#!/usr/bin/env python3
"""Read-only status / guarded CREATE-ONLY assembly of 126 independently admitted V2 runs.

Run inside the FaultWeave repository: python path/to/this/script --status
At complete admission only: python path/to/this/script --assemble
Existing captures, models, and training plans are never mutated. Validates artifacts
and keeps frozen feature key ORDER in aggregate JSONL (the fasttrack trainer requires it).
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys

NORMAL = 'NORMAL'
FAULTS = (
    'AUTHENTICATION_FAILURE_BURST',
    'CONNECTION_POOL_EXHAUSTION',
    'DATABASE_HIGH_LATENCY',
    'DATABASE_LOCK_CONTENTION',
    'DATABASE_UNAVAILABLE',
    'DOWNSTREAM_ERROR_BURST',
    'DOWNSTREAM_TIMEOUT',
    'HIGH_LOAD',
    'SERVICE_UNAVAILABLE',
)
LABELS = (NORMAL, *FAULTS)
SPLITS = ('train', 'validation', 'test')
TARGET = {(label, split): (12, 3, 3)[i] if label == NORMAL else (8, 2, 2)[i]
          for label in LABELS for i, split in enumerate(SPLITS)}


def fail(message):
    raise ValueError('STOP: ' + message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_dict(path):
    if path.is_symlink() or not path.is_file():
        fail('missing/symlink evidence ' + path.name)
    doc = json.loads(path.read_text(encoding='utf-8'),
                     parse_constant=lambda v: fail('non-finite JSON value ' + v))
    if not isinstance(doc, dict):
        fail('malformed evidence ' + path.name)
    return doc


def inside(root: Path, fragment: str):
    if not isinstance(fragment, str) or not fragment:
        fail('missing evidence path')
    path = root / fragment
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        fail('unsafe evidence reference ' + fragment)
    return path


def check_receipt_ref(root: Path, obj: dict, run_id: str):
    if not isinstance(obj, dict):
        fail('malformed evidence reference')
    path = inside(root, obj.get('path'))
    if sha(path.read_bytes()) != obj.get('sha256'):
        fail('admission evidence SHA256 mismatch ' + str(path))
    data = read_dict(path)
    if data.get('run_id') != run_id:
        fail('admission evidence refers to wrong run')
    return data, path


def admitted_rows(root, names):
    from datasets.final_features import FEATURE_NAMES
    if len(FEATURE_NAMES) != 65:
        fail('feature schema is not the frozen 65-feature schema')
    runs_root = root / 'data/datasets/v2/training-captures'
    if not runs_root.is_dir() or runs_root.is_symlink():
        fail('missing/unsafe training-captures directory')
    slots = {}
    run_ids = set()
    results = []
    # Inspect only already admitted directories; rejected/partial slots remain untouched.
    for directory in sorted(runs_root.iterdir()):
        if directory.is_symlink():
            fail('capture directory is symlink')
        if not directory.is_dir() or not (directory/'admission_receipt.json').is_file():
            continue
        name = directory.name
        labels = [label for label in LABELS if name.startswith(label.lower() + '-')]
        if len(labels) != 1:
            fail('unrecognized admitted capture folder ' + name)
        label = labels[0]
        match = re.fullmatch(re.escape(label.lower()) + r'-(\d{2})', name)
        if match is None:
            fail('invalid admitted slot name ' + name)
        slot = int(match.group(1))
        train, val, test = ((12, 3, 3) if label == NORMAL else (8, 2, 2))
        if not 1 <= slot <= train + val + test:
            fail('slot outside declared allocation ' + name)
        split = 'train' if slot <= train else 'validation' if slot <= train+val else 'test'
        if (label, slot) in slots:
            fail('duplicate label/slot')
        slots[label, slot] = True
        receipt = read_dict(directory/'admission_receipt.json')
        row = read_dict(directory/'feature_row.json')
        intent = read_dict(directory/'intent.json')
        run_id = row.get('run_id')
        if (not isinstance(run_id, str) or not run_id.startswith('v2train-')
                or len(run_id) != 24 or run_id in run_ids):
            fail('duplicate/invalid run_id in ' + name)
        run_ids.add(run_id)
        for item in (receipt, intent, row):
            if item.get('run_id') != run_id or item.get('label') != label or item.get('split') != split:
                fail('label/split/provenance mismatch in ' + name)
        if (intent.get('slot') != slot or intent.get('scenario_type') != ('normal' if label == NORMAL else 'known_fault')
                or receipt.get('training_admission') is not True
                or receipt.get('audit_verdict') != 'VALID_V2_TRAINING_RUN'
                or row.get('training_eligible') is not True
                or row.get('threshold_tuning_eligible') is not (split == 'validation')
                or row.get('window_seconds') != 60
                or row.get('interval') != ('normal' if label == NORMAL else 'fault')
                or row.get('scenario_type') != ('normal' if label == NORMAL else 'known_fault')):
            fail('admission and exact 60-second window mismatch in ' + name)
        manifest, manifest_path = check_receipt_ref(root, receipt.get('source_manifest'), run_id)
        audit, audit_path = check_receipt_ref(root, receipt.get('independent_audit'), run_id)
        if manifest_path != (directory/'source_manifest.json').resolve() or audit_path != (directory/'independent_audit.json').resolve():
            fail('admission points to another capture')
        if (audit.get('verdict') != 'VALID_V2_TRAINING_RUN' or audit.get('independent') is not True
                or audit.get('feature_count') != 65 or audit.get('window_seconds') != 60
                or audit.get('label') != label or audit.get('split') != split
                or manifest.get('window_seconds') != 60
                or manifest.get('label') != label or manifest.get('split') != split
                or manifest.get('training_eligible') is not True
                or manifest.get('admission_requires_separate_independent_audit') is not True
                or manifest.get('intent_sha256') != sha((directory/'intent.json').read_bytes())
                or audit.get('source_manifest_sha256') != sha(manifest_path.read_bytes())):
            fail('source/audit mismatch in ' + name)
        artifacts = manifest.get('artifacts')
        if not isinstance(artifacts, dict) or 'feature_row.json' not in artifacts:
            fail('missing source artifacts ' + name)
        for filename, entry in artifacts.items():
            if not isinstance(entry, dict) or not re.fullmatch(r'[\w.\-]+', filename):
                fail('unsafe source artifact entry')
            file_path = inside(root, str((directory/filename).relative_to(root)))
            raw = file_path.read_bytes()
            if len(raw) != entry.get('bytes') or sha(raw) != entry.get('sha256'):
                fail('source artifact changed after independent admission: ' + name + '/' + filename)
        values = row.get('features')
        if (row.get('schema_version') != 'faultweave-v2-feature-row.1' or
            not isinstance(values, dict) or set(values) != set(FEATURE_NAMES) or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in values.values())):
            fail('invalid frozen feature vector ' + name)
        # Deliberately restore canonical frozen order: json.dumps(sort_keys=True) on
        # individual capture files alphabetized keys; downstream fasttrack requires order.
        row['features'] = {feature: values[feature] for feature in FEATURE_NAMES}
        results.append((label, split, name, row, receipt))
    return results


def overview(rows):
    counts = Counter((label, split) for label, split, *_ in rows)
    total = len(rows)
    print(f'V2 ADMISSION SUMMARY — {total}/126 saved and independently admitted')
    for label in LABELS:
        found = sum(counts[label, sp] for sp in SPLITS)
        budget = 18 if label == NORMAL else 12
        print(f'{label:32} {found:2}/{budget}: '
              + ', '.join(f'{sp}={counts[label,sp]}/{TARGET[label,sp]}' for sp in SPLITS))
    missing = [(label, split, TARGET[label,split] - counts[label,split])
               for label in LABELS for split in SPLITS
               if counts[label,split] != TARGET[label,split]]
    if any(n < 0 for _, _, n in missing):
        fail('over-admission detected')
    print('STATUS:', 'READY_TO_ASSEMBLE' if not missing else 'INCOMPLETE: leave existing captures untouched')
    return not missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--status', action='store_true')
    mode.add_argument('--assemble', action='store_true')
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if not (root/'scripts/faultweave_v2_fasttrack.py').is_file():
        fail('run from FaultWeave repository root')
    sys.path.insert(0, str(root))
    rows = admitted_rows(root, None)
    complete = overview(rows)
    if args.status:
        print('READ ONLY: no model, source capture, or plan files changed')
        return
    if not complete:
        fail('126 required independently admitted captures are not complete; assembly blocked')
    contents = ''.join(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
                       for *_, row, receipt in rows).encode('utf-8')
    receipts = [receipt for *_, row, receipt in rows]
    book = {'schema_version': 'faultweave-v2-run-admission.1',
            'feature_file_sha256': sha(contents), 'independent_audit_required': True,
            'runs': receipts}
    book_bytes = (json.dumps(book, ensure_ascii=False, indent=2, sort_keys=True)+'\n').encode()
    output = root / 'data/datasets/v2/assembled/verified-126-v1'
    if output.exists() or output.is_symlink():
        fail('output already exists; never overwrite assembled dataset')
    # Verify all assertions before writing; assembly is create-only, and never changes inputs.
    from scripts.faultweave_v2_fasttrack import verify_feature_rows
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    try:
        (output/'features.jsonl').write_bytes(contents)
        (output/'admission_book.json').write_bytes(book_bytes)
        ready = verify_feature_rows(root, output/'features.jsonl', output/'admission_book.json')
        print('ASSEMBLED:', output)
        print(json.dumps(ready, indent=2))
    except BaseException:
        print('STOP: assembly incomplete; created output left for inspection, source captures unchanged')
        raise

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
