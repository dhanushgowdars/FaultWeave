"""Offline refusal tests for the independent 2.5 RPS calibration audit."""
import json

import pytest

from scripts.phase19c2h_integrity_audit import run


def test_absent_calibration_directory_rejected(tmp_path):
    with pytest.raises(ValueError, match="folder not found"):
        run(tmp_path, tmp_path / "data/datasets/v2/calibration/no-run")


def test_out_of_root_folder_rejected(tmp_path):
    other = tmp_path / "pilot"
    other.mkdir()
    with pytest.raises(ValueError, match="immediate child"):
        run(tmp_path, other)


def test_missing_artifact_rejected(tmp_path):
    p = tmp_path / "data/datasets/v2/calibration/v2cal-0123456789abcdef"
    p.mkdir(parents=True)
    with pytest.raises(ValueError, match="missing requests.jsonl"):
        run(tmp_path, p)


def _minimal_dir(tmp_path, manifest):
    p = tmp_path / "data/datasets/v2/calibration/v2cal-0123456789abcdef"
    p.mkdir(parents=True)
    for name in ("requests.jsonl", "events.jsonl", "candidate_feature.json"):
        (p / name).write_text('[]\n')
    (p / "manifest.json").write_text(json.dumps(manifest))
    return p


def test_wrong_run_identity_rejected(tmp_path):
    p = _minimal_dir(tmp_path, {"run_id": "v2cal-wrong"})
    with pytest.raises(ValueError, match="run IDs differ"):
        run(tmp_path, p)


def test_wrong_schema_rejected(tmp_path):
    p = _minimal_dir(tmp_path, {"run_id": "v2cal-0123456789abcdef", "schema_version": "19c2c.pilot.1"})
    with pytest.raises(ValueError, match="unexpected manifest schema"):
        run(tmp_path, p)


def test_eligible_calibration_rejected(tmp_path):
    p = _minimal_dir(tmp_path, {"run_id": "v2cal-0123456789abcdef", "schema_version": "19c2h.calibration.1", "training_eligible": True})
    with pytest.raises(ValueError, match="accidentally marked eligible"):
        run(tmp_path, p)


def test_unverified_hash_rejected(tmp_path):
    p = _minimal_dir(tmp_path, {"run_id": "v2cal-0123456789abcdef", "schema_version": "19c2h.calibration.1", "training_eligible": False, "threshold_tuning_eligible": False, "split": "diagnostic_only", "known_fault_label": None, "outgoing_header": "X-Run-ID", "artifacts": {}})
    with pytest.raises(ValueError, match="integrity check failed"):
        run(tmp_path, p)
