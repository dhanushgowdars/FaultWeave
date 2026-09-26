from __future__ import annotations

import json
from pathlib import Path

import joblib
import pytest

from app.intelligence_artifacts import ArtifactLoadError, load_frozen_artifacts

CLASSES = tuple(f"FAULT_{index}" for index in range(9))
FEATURES = ("feature_a", "feature_b", "feature_c")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _build_artifact_tree(root: Path) -> None:
    main = root / "rich-v2-60s"
    isolation = main / "isolation-forest"
    classifier = main / "xgboost-known-fault"
    open_set = main / "class-conditional-open-set"
    temporal = root / "rich-v2-10s" / "temporal-isolation-forest"

    _write_json(
        isolation / "metadata.json",
        {"window_seconds": 60, "feature_names": list(FEATURES), "threshold": 0.125},
    )
    isolation.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": "main-if", "scaler": "main-scaler"}, isolation / "model.joblib")

    _write_json(
        classifier / "metadata.json",
        {"window_seconds": 60, "feature_names": list(FEATURES), "classes": list(CLASSES)},
    )
    classifier.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": "xgb", "classes": list(CLASSES)}, classifier / "model.joblib")

    _write_json(
        open_set / "metadata.json",
        {
            "window_seconds": 60,
            "feature_names": list(FEATURES),
            "open_set_label": "UNKNOWN ABNORMAL PATTERN",
            "anomaly_threshold": 0.125,
            "class_support_limits": {name: 2.0 for name in CLASSES},
            "class_support_profiles": {
                name: {"centers": [0.0] * len(FEATURES), "scales": [1.0] * len(FEATURES)}
                for name in CLASSES
            },
        },
    )

    _write_json(
        temporal / "metadata.json",
        {"window_seconds": 10, "feature_names": list(FEATURES), "threshold": 0.25},
    )
    temporal.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": "temporal-if", "scaler": "temporal-scaler"}, temporal / "model.joblib")


def test_loads_complete_frozen_intelligence_bundle(tmp_path: Path) -> None:
    _build_artifact_tree(tmp_path)

    artifacts = load_frozen_artifacts(tmp_path)

    assert artifacts.feature_names == FEATURES
    assert artifacts.classes == CLASSES
    assert artifacts.main_anomaly_threshold == 0.125
    assert artifacts.temporal_anomaly_threshold == 0.25
    assert artifacts.open_set_label == "UNKNOWN ABNORMAL PATTERN"
    assert artifacts.readiness() == {
        "status": "ready",
        "main_window_seconds": 60,
        "temporal_window_seconds": 10,
        "feature_count": 3,
        "known_class_count": 9,
        "open_set_label": "UNKNOWN ABNORMAL PATTERN",
    }


def test_rejects_feature_schema_drift(tmp_path: Path) -> None:
    _build_artifact_tree(tmp_path)
    metadata_path = tmp_path / "rich-v2-10s" / "temporal-isolation-forest" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["feature_names"] = ["different_feature"]
    _write_json(metadata_path, metadata)

    with pytest.raises(ArtifactLoadError, match="feature schema differs"):
        load_frozen_artifacts(tmp_path)


def test_rejects_open_set_threshold_drift(tmp_path: Path) -> None:
    _build_artifact_tree(tmp_path)
    metadata_path = tmp_path / "rich-v2-60s" / "class-conditional-open-set" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["anomaly_threshold"] = 0.5
    _write_json(metadata_path, metadata)

    with pytest.raises(ArtifactLoadError, match="threshold differs"):
        load_frozen_artifacts(tmp_path)


def test_rejects_missing_model_artifact(tmp_path: Path) -> None:
    _build_artifact_tree(tmp_path)
    (tmp_path / "rich-v2-60s" / "xgboost-known-fault" / "model.joblib").unlink()

    with pytest.raises(ArtifactLoadError, match="unable to load frozen model artifact"):
        load_frozen_artifacts(tmp_path)
