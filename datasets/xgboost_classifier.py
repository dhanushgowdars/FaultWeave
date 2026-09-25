from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
from sklearn.metrics import accuracy_score, f1_score
from xgboost import XGBClassifier

from experiments.manifest import sha256_file, write_json

from .final_features import FEATURE_NAMES
from .isolation_forest_model import FeatureRow, read_feature_rows

MODEL_SCHEMA_VERSION = "1.0"
RANDOM_STATE = 20260922
MODEL_CONFIG = {"n_estimators": 160, "max_depth": 4, "learning_rate": 0.08, "subsample": 0.9}


def known_fault_rows(rows: list[FeatureRow], split: str) -> list[FeatureRow]:
    selected = [
        row
        for row in rows
        if row.split == split
        and row.scenario_type == "known_fault"
        and row.interval == "fault"
        and row.label != "NORMAL"
        and row.training_eligible
    ]
    if not selected:
        raise ValueError(f"no eligible known-fault {split} windows")
    return selected


def vectors(rows: list[FeatureRow]) -> list[tuple[float, ...]]:
    return [row.features for row in rows]


def metric_summary(
    model: XGBClassifier, rows: list[FeatureRow], classes: list[str]
) -> dict[str, Any]:
    actual = [classes.index(row.label) for row in rows]
    predicted = [int(value) for value in model.predict(vectors(rows))]
    probabilities = model.predict_proba(vectors(rows))
    confidence = [float(max(item)) for item in probabilities]
    return {
        "row_count": len(rows),
        "accuracy": round(float(accuracy_score(actual, predicted)), 6),
        "macro_f1": round(float(f1_score(actual, predicted, average="macro", zero_division=0)), 6),
        "mean_confidence": round(sum(confidence) / len(confidence), 6),
        "class_counts": dict(sorted(Counter(row.label for row in rows).items())),
    }


def build_xgboost_classifier(
    feature_path: Path, output_root: Path, report_path: Path
) -> dict[str, Any]:
    rows = read_feature_rows(feature_path)
    window_sizes = {row.window_seconds for row in rows}
    if len(window_sizes) != 1:
        raise ValueError("feature input must contain exactly one window size")
    window_seconds = window_sizes.pop()
    train = known_fault_rows(rows, "train")
    validation = known_fault_rows(rows, "validation")
    test = known_fault_rows(rows, "test")
    sealed = [row for row in rows if row.scenario_type == "sealed_unknown"]
    if sealed:
        # The classifier deliberately never accepts sealed rows, even for model evaluation.
        sealed_count = len(sealed)
    else:
        raise ValueError("sealed-unknown evidence is required to prove isolation")
    classes = sorted({row.label for row in train})
    if len(classes) != 9:
        raise ValueError("expected exactly nine known fault classes in training")
    if set(row.label for row in validation + test) != set(classes):
        raise ValueError("validation and test must contain every known fault class")
    model = XGBClassifier(
        objective="multi:softprob",
        num_class=len(classes),
        random_state=RANDOM_STATE,
        n_jobs=-1,
        eval_metric="mlogloss",
        **MODEL_CONFIG,
    )
    model.fit(vectors(train), [classes.index(row.label) for row in train])
    output_root.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "classes": classes}, output_root / "model.joblib")
    metadata = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "model_type": "XGBoost known-fault classifier",
        "window_seconds": window_seconds,
        "feature_names": list(FEATURE_NAMES),
        "classes": classes,
        "training": {
            "rows": len(train),
            "run_count": len({row.run_id for row in train}),
            "sealed_unknown_rows": 0,
            "normal_rows": 0,
        },
        "input": {
            "feature_sha256": sha256_file(feature_path),
            "feature_report_sha256": sha256_file(report_path),
        },
        "config": {**MODEL_CONFIG, "random_state": RANDOM_STATE},
    }
    metrics = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "validation": metric_summary(model, validation, classes),
        "known_test": metric_summary(model, test, classes),
        "sealed_unknown": {"rows_excluded": sealed_count, "evaluation_used": False},
    }
    write_json(output_root / "metadata.json", metadata)
    write_json(output_root / "metrics.json", metrics)
    return {
        "metadata": metadata,
        "metrics": metrics,
        "model_sha256": sha256_file(output_root / "model.joblib"),
    }


def validate_xgboost_artifacts(root: Path) -> list[str]:
    try:
        metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unable to read classifier artifacts: {exc}"]
    errors: list[str] = []
    if not (root / "model.joblib").is_file():
        errors.append("serialized XGBoost model is missing")
    if metadata.get("model_type") != "XGBoost known-fault classifier":
        errors.append("model metadata does not identify XGBoost")
    if tuple(metadata.get("feature_names", [])) != FEATURE_NAMES:
        errors.append("classifier feature schema changed")
    if len(metadata.get("classes", [])) != 9:
        errors.append("classifier does not declare nine known fault classes")
    training = metadata.get("training", {})
    if training.get("sealed_unknown_rows") != 0 or training.get("normal_rows") != 0:
        errors.append("classifier training isolation contract was violated")
    if metrics.get("sealed_unknown", {}).get("evaluation_used") is not False:
        errors.append("sealed unknown rows were used by the known-fault classifier")
    return errors
