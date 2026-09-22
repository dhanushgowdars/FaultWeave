from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.preprocessing import StandardScaler

from experiments.manifest import sha256_file, write_json

from .final_features import FEATURE_NAMES

MODEL_SCHEMA_VERSION = "1.0"
WINDOW_SECONDS = 30
MODEL_RANDOM_STATE = 20260922
MODEL_CONFIG = {"n_estimators": 300, "max_samples": "auto", "contamination": "auto"}


@dataclass(frozen=True)
class FeatureRow:
    run_id: str
    split: str
    scenario_type: str
    training_eligible: bool
    threshold_tuning_eligible: bool
    label: str
    interval: str
    features: tuple[float, ...]


def read_feature_rows(path: Path) -> list[FeatureRow]:
    rows: list[FeatureRow] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            item = json.loads(line)
            values = item.get("features")
            if not isinstance(values, dict) or set(values) != set(FEATURE_NAMES):
                raise ValueError("feature row does not match frozen feature schema")
            vector = tuple(float(values[name]) for name in FEATURE_NAMES)
            if any(not math.isfinite(value) for value in vector):
                raise ValueError("feature row contains non-finite values")
            rows.append(
                FeatureRow(
                    run_id=str(item["run_id"]),
                    split=str(item["split"]),
                    scenario_type=str(item["scenario_type"]),
                    training_eligible=bool(item["training_eligible"]),
                    threshold_tuning_eligible=bool(item["threshold_tuning_eligible"]),
                    label=str(item["label"]),
                    interval=str(item["interval"]),
                    features=vector,
                )
            )
    if not rows:
        raise ValueError("feature input is empty")
    return rows


def normal_training_rows(rows: list[FeatureRow]) -> list[FeatureRow]:
    selected = [
        row
        for row in rows
        if row.split == "train"
        and row.scenario_type == "normal"
        and row.label == "NORMAL"
        and row.training_eligible
    ]
    if not selected:
        raise ValueError("no eligible normal training windows")
    return selected


def validation_rows(rows: list[FeatureRow]) -> list[FeatureRow]:
    selected = [
        row
        for row in rows
        if row.split == "validation"
        and row.threshold_tuning_eligible
        and row.scenario_type != "sealed_unknown"
    ]
    if {row.label == "NORMAL" for row in selected} != {False, True}:
        raise ValueError("validation rows must contain normal and known-fault windows")
    return selected


def _vectors(rows: list[FeatureRow]) -> list[tuple[float, ...]]:
    return [row.features for row in rows]


def anomaly_scores(
    model: IsolationForest, scaler: StandardScaler, rows: list[FeatureRow]
) -> list[float]:
    # Higher score means more anomalous, which makes threshold interpretation direct.
    return [-float(score) for score in model.decision_function(scaler.transform(_vectors(rows)))]


def choose_threshold(scores: list[float], labels: list[bool]) -> tuple[float, dict[str, float]]:
    if len(scores) != len(labels) or not scores or len(set(labels)) != 2:
        raise ValueError("threshold tuning requires aligned normal and abnormal validation scores")
    candidates = sorted(set(scores))
    best: tuple[float, float, float, float] | None = None
    for threshold in candidates:
        predicted = [score >= threshold for score in scores]
        f1 = f1_score(labels, predicted, zero_division=0)
        recall = recall_score(labels, predicted, zero_division=0)
        precision = precision_score(labels, predicted, zero_division=0)
        candidate = (float(f1), float(recall), float(precision), float(threshold))
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    return best[3], {
        "validation_f1": best[0],
        "validation_recall": best[1],
        "validation_precision": best[2],
    }


def evaluate(scores: list[float], rows: list[FeatureRow], threshold: float) -> dict[str, Any]:
    labels = [row.label != "NORMAL" for row in rows]
    predicted = [score >= threshold for score in scores]
    normal = [not label for label in labels]
    return {
        "row_count": len(rows),
        "normal_rows": sum(normal),
        "known_abnormal_rows": sum(labels),
        "precision": round(float(precision_score(labels, predicted, zero_division=0)), 6),
        "recall": round(float(recall_score(labels, predicted, zero_division=0)), 6),
        "f1": round(float(f1_score(labels, predicted, zero_division=0)), 6),
        "normal_false_positive_rate": round(
            sum(
                predicted_item and normal_item
                for predicted_item, normal_item in zip(predicted, normal, strict=True)
            )
            / max(1, sum(normal)),
            6,
        ),
        "predicted_anomaly_rows": sum(predicted),
    }


def build_isolation_forest(
    feature_path: Path, output_root: Path, feature_report_path: Path
) -> dict[str, Any]:
    rows = read_feature_rows(feature_path)
    training = normal_training_rows(rows)
    validation = validation_rows(rows)
    scaler = StandardScaler().fit(_vectors(training))
    model = IsolationForest(random_state=MODEL_RANDOM_STATE, n_jobs=-1, **MODEL_CONFIG)
    model.fit(scaler.transform(_vectors(training)))
    threshold, tuning = choose_threshold(
        anomaly_scores(model, scaler, validation), [row.label != "NORMAL" for row in validation]
    )
    known_test = [
        row for row in rows if row.split == "test" and row.scenario_type != "sealed_unknown"
    ]
    sealed_unknown = [row for row in rows if row.scenario_type == "sealed_unknown"]
    if not known_test or not sealed_unknown:
        raise ValueError("test and sealed-unknown evaluation partitions are required")
    known_metrics = evaluate(anomaly_scores(model, scaler, known_test), known_test, threshold)
    unknown_scores = anomaly_scores(model, scaler, sealed_unknown)
    unknown_metrics = {
        "row_count": len(sealed_unknown),
        "detected_anomaly_rate": round(
            sum(score >= threshold for score in unknown_scores) / len(unknown_scores), 6
        ),
        "families": dict(sorted(Counter(row.label for row in sealed_unknown).items())),
        "evaluation_only": True,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    joblib.dump({"scaler": scaler, "model": model}, output_root / "model.joblib")
    metadata = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "model_type": "IsolationForest",
        "window_seconds": WINDOW_SECONDS,
        "feature_names": list(FEATURE_NAMES),
        "threshold": threshold,
        "training": {
            "rows": len(training),
            "run_count": len({row.run_id for row in training}),
            "scenario_types": ["normal"],
            "sealed_unknown_rows": 0,
        },
        "input": {
            "feature_path": str(feature_path),
            "feature_sha256": sha256_file(feature_path),
            "feature_report_sha256": sha256_file(feature_report_path),
        },
        "config": {**MODEL_CONFIG, "random_state": MODEL_RANDOM_STATE},
    }
    metrics = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "threshold_tuning": tuning,
        "known_test": known_metrics,
        "sealed_unknown": unknown_metrics,
    }
    write_json(output_root / "metadata.json", metadata)
    write_json(output_root / "metrics.json", metrics)
    return {
        "metadata": metadata,
        "metrics": metrics,
        "model_sha256": sha256_file(output_root / "model.joblib"),
    }


def validate_isolation_forest_artifacts(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
        model_file = root / "model.joblib"
    except (OSError, ValueError) as exc:
        return [f"unable to read model artifacts: {exc}"]
    if not model_file.is_file() or model_file.stat().st_size == 0:
        errors.append("serialized Isolation Forest model is missing")
    if metadata.get("model_type") != "IsolationForest":
        errors.append("model metadata does not identify IsolationForest")
    if tuple(metadata.get("feature_names", [])) != FEATURE_NAMES:
        errors.append("model metadata feature schema changed")
    training = metadata.get("training", {})
    if training.get("scenario_types") != ["normal"] or training.get("sealed_unknown_rows") != 0:
        errors.append("model training isolation contract was violated")
    if not isinstance(metadata.get("threshold"), int | float):
        errors.append("model threshold is missing")
    if metrics.get("sealed_unknown", {}).get("evaluation_only") is not True:
        errors.append("sealed unknown evaluation policy is missing")
    return errors
