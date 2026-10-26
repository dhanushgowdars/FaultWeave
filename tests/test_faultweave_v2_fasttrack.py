import hashlib
import json
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

from scripts.faultweave_v2_fasttrack import (
    KNOWN_FAMILIES, Stop, compact_plan, verify_feature_rows, candidate_training,
    canonical_bytes, sha256,
)

PLAN_SOURCE = Path(__file__).resolve().parents[1] / 'data/datasets/v2/plan/phase19c2g-reachability-proposal.json'
FEATURES = tuple(f'feature_{i}' for i in range(65))


def root_fixture(tmp_path, monkeypatch):
    path = tmp_path / 'data/datasets/v2/plan'
    path.mkdir(parents=True)
    (path / PLAN_SOURCE.name).write_bytes(PLAN_SOURCE.read_bytes())
    fake = types.ModuleType('datasets.final_features')
    fake.FEATURE_NAMES = FEATURES
    monkeypatch.setitem(sys.modules, 'datasets.final_features', fake)
    return tmp_path


def write_dataset(root):
    draft = compact_plan(root)
    data = root / 'data/datasets/v2/ingest'
    data.mkdir(parents=True)
    rows, receipts = [], []
    for index, r in enumerate(draft['runs']):
        label = r['fault_id'] or 'NORMAL'
        group = 0 if label == 'NORMAL' else 1 + sorted(KNOWN_FAMILIES).index(label)
        vector = {name: float(group * 10 + i / 1000 + (index % 7) / 10)
                  for i, name in enumerate(FEATURES)}
        rows.append({'run_id': r['run_id'], 'split': r['split'], 'scenario_type': r['scenario_type'],
                     'label': label, 'window_seconds': 60,
                     'training_eligible': True,
                     'interval': 'normal' if label == 'NORMAL' else 'fault', 'features': vector})
        manifest = {'run_id': r['run_id'], 'split': r['split'],
                    'scenario_type': r['scenario_type'], 'label': label,
                    'training_eligible': True, 'window_seconds': 60}
        audit = {'run_id': r['run_id'], 'verdict': 'VALID_V2_TRAINING_RUN', 'independent': True}
        manifest_path = data / (r['run_id'] + '-manifest.json')
        audit_path = data / (r['run_id'] + '-audit.json')
        manifest_path.write_bytes(canonical_bytes(manifest))
        audit_path.write_bytes(canonical_bytes(audit))
        receipts.append({'run_id': r['run_id'], 'split': r['split'],
                         'scenario_type': r['scenario_type'], 'label': label,
                         'training_admission': True, 'audit_verdict': 'VALID_V2_TRAINING_RUN',
                         'source_manifest': {'path': str(manifest_path.relative_to(root)), 'sha256': sha256(manifest_path)},
                         'independent_audit': {'path': str(audit_path.relative_to(root)), 'sha256': sha256(audit_path)}})
    feature_file = data / 'admitted-features.jsonl'
    feature_file.write_text(''.join(json.dumps(x) + '\n' for x in rows))
    receipts_file = data / 'admissions.json'
    receipts_file.write_bytes(canonical_bytes({
        'schema_version': 'faultweave-v2-run-admission.1',
        'feature_file_sha256': sha256(feature_file), 'independent_audit_required': True,
        'runs': receipts,
    }))
    return feature_file, receipts_file, rows


def test_compact_plan_126_and_stable(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    a = compact_plan(root)
    assert a == compact_plan(root)
    assert len(a['runs']) == 126
    assert all(r['execution_allowed'] is False and r['training_eligible'] is False
               and r['target_rps'] is None for r in a['runs'])
    assert Counter(r['split'] for r in a['runs']) == {'train': 84, 'validation': 21, 'test': 21}
    labels = Counter(r['fault_id'] or 'NORMAL' for r in a['runs'])
    assert labels['NORMAL'] == 18
    assert all(labels[cls] == 12 for cls in KNOWN_FAMILIES)


def test_parent_approval_tamper_refused(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    path = root / 'data/datasets/v2/plan' / PLAN_SOURCE.name
    data = json.loads(path.read_text())
    data['runs'][0]['training_eligible'] = True
    path.write_bytes(canonical_bytes(data))
    with pytest.raises(Stop, match='approval'):
        compact_plan(root)


def test_admission_gate_and_no_unknown_holdout(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    f, r, _ = write_dataset(root)
    result = verify_feature_rows(root, f, r)
    assert result['feature_rows'] == 126
    assert result['known_classes'] == 9
    assert result['unknown_holdout_in_training'] == 0


def test_reject_diagnostic_receipt(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    f, r, _ = write_dataset(root)
    data = json.loads(r.read_text())
    data['runs'][0]['training_admission'] = False
    r.write_bytes(canonical_bytes(data))
    with pytest.raises(Stop, match='unapproved'):
        verify_feature_rows(root, f, r)


def test_reject_changed_source_audit(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    f, r, _ = write_dataset(root)
    data = json.loads(r.read_text())
    path = root / data['runs'][0]['independent_audit']['path']
    path.write_bytes(b'{}')
    with pytest.raises(Stop, match='hash mismatch'):
        verify_feature_rows(root, f, r)


def test_reject_changed_feature_file(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    f, r, _ = write_dataset(root)
    with f.open('a') as stream: stream.write('\n')
    with pytest.raises(Stop, match='changed'):
        verify_feature_rows(root, f, r)


def test_reject_unknown_labels(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    f, r, rows = write_dataset(root)
    rows[0]['label'] = 'LATENCY_JITTER_PARTIAL_DEGRADATION'
    f.write_text(''.join(json.dumps(x) + '\n' for x in rows))
    receipts = json.loads(r.read_text())
    receipts['feature_file_sha256'] = sha256(f)
    r.write_bytes(canonical_bytes(receipts))
    with pytest.raises(Stop, match='disagree'):
        verify_feature_rows(root, f, r)


def test_train_candidate_without_promoting_models(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    f, r, _ = write_dataset(root)
    output = Path('data/datasets/v2/models/candidate-synthetic-test')
    result = candidate_training(root, f, r, output)
    assert result['status'] == 'PRELIMINARY_V2_CANDIDATE_NOT_DEPLOYED'
    assert result['sealed_unknown_evaluated'] is False
    d = root / output
    assert (d / 'isolation_forest.joblib').is_file()
    assert (d / 'xgboost_open_set.joblib').is_file()
    evaluation = json.loads((d / 'evaluation.json').read_text())
    assert evaluation['sealed_unknown_families_used_for_training_or_calibration'] == 0
    assert evaluation['test_metrics']['confusion_matrix']
    with pytest.raises(Stop, match='new immediate child'):
        candidate_training(root, f, r, output)


def test_no_v1_output_or_external_path(tmp_path, monkeypatch):
    root = root_fixture(tmp_path, monkeypatch)
    f, r, _ = write_dataset(root)
    with pytest.raises(Stop, match='new immediate child'):
        candidate_training(root, f, r, Path('data/datasets/final/models'))
