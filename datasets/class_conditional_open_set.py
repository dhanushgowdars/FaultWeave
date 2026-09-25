from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median, pstdev
from typing import Any

import joblib

from experiments.manifest import sha256_file, write_json

from .final_features import FEATURE_NAMES
from .isolation_forest_model import FeatureRow, anomaly_scores, read_feature_rows
from .open_set_rejection import OPEN_SET_LABEL
from .xgboost_classifier import known_fault_rows, vectors

ARTIFACT_SCHEMA_VERSION = "1.0"
SUPPORT_MULTIPLIER = 1.5
MINIMUM_SCALE = 1e-3


def support_profile(rows: list[FeatureRow]) -> dict[str, list[float]]:
    columns = list(zip(*(row.features for row in rows), strict=True))
    centers = [float(median(column)) for column in columns]
    scales = [
        max(float(pstdev(column)), abs(center) * 1e-4, MINIMUM_SCALE)
        for column, center in zip(columns, centers, strict=True)
    ]
    return {"centers": centers, "scales": scales}


def support_score(features: tuple[float, ...], profile: dict[str, list[float]]) -> float:
    return max(
        abs(value - center) / scale
        for value, center, scale in zip(
            features, profile["centers"], profile["scales"], strict=True
        )
    )


def supported_label(
    anomaly_score: float,
    anomaly_threshold: float,
    class_name: str,
    class_support_score: float,
    class_support_limit: float,
) -> str:
    if anomaly_score < anomaly_threshold:
        return "NORMAL"
    if class_support_score > class_support_limit:
        return OPEN_SET_LABEL
    return class_name


def _load_models(isolation_root: Path, classifier_root: Path) -> tuple[Any, Any, list[str], float]:
    isolation_metadata = json.loads((isolation_root / "metadata.json").read_text("utf-8"))
    classifier_metadata = json.loads((classifier_root / "metadata.json").read_text("utf-8"))
    for metadata in (isolation_metadata, classifier_metadata):
        if metadata.get("window_seconds") != 60:
            raise ValueError("class-conditional open-set policy requires 60-second features")
        if tuple(metadata.get("feature_names", [])) != FEATURE_NAMES:
            raise ValueError("model feature schema does not match rich features")
    isolation = joblib.load(isolation_root / "model.joblib")
    classifier = joblib.load(classifier_root / "model.joblib")
    return (
        isolation,
        classifier["model"],
        list(classifier["classes"]),
        float(isolation_metadata["threshold"]),
    )


def _class_probabilities(model: Any, rows: list[FeatureRow]) -> list[list[float]]:
    return [[float(value) for value in item] for item in model.predict_proba(vectors(rows))]


def _profiles(training: list[FeatureRow], classes: list[str]) -> dict[str, dict[str, list[float]]]:
    return {
        class_name: support_profile([row for row in training if row.label == class_name])
        for class_name in classes
    }


def _calibrate_limits(
    validation: list[FeatureRow],
    classifier: Any,
    classes: list[str],
    profiles: dict[str, dict[str, list[float]]],
) -> dict[str, float]:
    probabilities = _class_probabilities(classifier, validation)
    scores: dict[str, list[float]] = {class_name: [] for class_name in classes}
    for row, probability in zip(validation, probabilities, strict=True):
        predicted = classes[max(range(len(probability)), key=probability.__getitem__)]
        if predicted == row.label:
            scores[predicted].append(support_score(row.features, profiles[predicted]))
    if any(not values for values in scores.values()):
        raise ValueError("each known class requires correctly classified validation support")
    return {class_name: max(values) * SUPPORT_MULTIPLIER for class_name, values in scores.items()}


def _predictions(
    rows: list[FeatureRow],
    isolation: Any,
    classifier: Any,
    classes: list[str],
    anomaly_threshold: float,
    profiles: dict[str, dict[str, list[float]]],
    limits: dict[str, float],
) -> list[dict[str, Any]]:
    anomaly = anomaly_scores(isolation["model"], isolation["scaler"], rows)
    probabilities = _class_probabilities(classifier, rows)
    records: list[dict[str, Any]] = []
    for row, anomaly_score, probability in zip(rows, anomaly, probabilities, strict=True):
        class_index = max(range(len(probability)), key=probability.__getitem__)
        class_name = classes[class_index]
        score = support_score(row.features, profiles[class_name])
        records.append(
            {
                "run_id": row.run_id,
                "scenario_type": row.scenario_type,
                "actual_label": row.label,
                "predicted_label": supported_label(
                    anomaly_score,
                    anomaly_threshold,
                    class_name,
                    score,
                    limits[class_name],
                ),
                "anomaly_score": round(anomaly_score, 8),
                "known_class": class_name,
                "known_class_confidence": round(probability[class_index], 8),
                "class_support_score": round(score, 8),
                "class_support_limit": round(limits[class_name], 8),
            }
        )
    return records


def _known_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    fault = [item for item in records if item["actual_label"] != "NORMAL"]
    normal = [item for item in records if item["actual_label"] == "NORMAL"]
    return {
        "known_fault_rows": len(fault),
        "known_fault_accuracy": round(
            sum(item["predicted_label"] == item["actual_label"] for item in fault)
            / max(1, len(fault)),
            6,
        ),
        "known_fault_rejection_rate": round(
            sum(item["predicted_label"] == OPEN_SET_LABEL for item in fault) / max(1, len(fault)),
            6,
        ),
        "normal_rows": len(normal),
        "normal_accuracy": round(
            sum(item["predicted_label"] == "NORMAL" for item in normal) / max(1, len(normal)),
            6,
        ),
    }


def _development_unknown_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    families: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        families.setdefault(str(item["actual_label"]), []).append(item)
    return {
        "rows": len(records),
        "unknown_rejection_rate": round(
            sum(item["predicted_label"] == OPEN_SET_LABEL for item in records)
            / max(1, len(records)),
            6,
        ),
        "families": {
            name: {
                "rows": len(items),
                "unknown_rejection_rate": round(
                    sum(item["predicted_label"] == OPEN_SET_LABEL for item in items) / len(items),
                    6,
                ),
            }
            for name, items in sorted(families.items())
        },
        "predicted_labels": dict(
            sorted(Counter(item["predicted_label"] for item in records).items())
        ),
        "evaluation_only": True,
        "status": "development_unknown_not_fresh_holdout",
    }


def build_class_conditional_open_set(
    feature_path: Path,
    isolation_root: Path,
    classifier_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    rows = read_feature_rows(feature_path)
    if {row.window_seconds for row in rows} != {60}:
        raise ValueError("expected only 60-second feature rows")
    training = known_fault_rows(rows, "train")
    validation = known_fault_rows(rows, "validation")
    known_test = [
        row for row in rows if row.split == "test" and row.scenario_type != "sealed_unknown"
    ]
    development_unknown = [
        row for row in rows if row.scenario_type == "sealed_unknown" and row.label != "NORMAL"
    ]
    isolation, classifier, classes, anomaly_threshold = _load_models(
        isolation_root, classifier_root
    )
    profiles = _profiles(training, classes)
    limits = _calibrate_limits(validation, classifier, classes, profiles)
    known_predictions = _predictions(
        known_test,
        isolation,
        classifier,
        classes,
        anomaly_threshold,
        profiles,
        limits,
    )
    unknown_predictions = _predictions(
        development_unknown,
        isolation,
        classifier,
        classes,
        anomaly_threshold,
        profiles,
        limits,
    )
    metadata = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_type": "60-second class-conditional open-set support",
        "window_seconds": 60,
        "feature_names": list(FEATURE_NAMES),
        "open_set_label": OPEN_SET_LABEL,
        "anomaly_threshold": anomaly_threshold,
        "support_metric": "maximum per-feature standardized class deviation",
        "support_multiplier": SUPPORT_MULTIPLIER,
        "class_support_limits": limits,
        "class_support_profiles": profiles,
        "calibration": {
            "source": "known_fault_training_and_validation_only",
            "sealed_unknown_rows": 0,
            "support_multiplier_source": "leave_one-known-fault-out stable plateau",
        },
        "inputs": {
            "feature_sha256": sha256_file(feature_path),
            "isolation_metadata_sha256": sha256_file(isolation_root / "metadata.json"),
            "classifier_metadata_sha256": sha256_file(classifier_root / "metadata.json"),
        },
    }
    metrics = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "known_test": _known_metrics(known_predictions),
        "development_unknown": _development_unknown_metrics(unknown_predictions),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / "metadata.json", metadata)
    write_json(output_root / "metrics.json", metrics)
    write_json(output_root / "known_test_predictions.json", known_predictions)
    write_json(output_root / "development_unknown_predictions.json", unknown_predictions)
    return {"metadata": metadata, "metrics": metrics}


def validate_class_conditional_artifacts(root: Path) -> list[str]:
    try:
        metadata = json.loads((root / "metadata.json").read_text("utf-8"))
        metrics = json.loads((root / "metrics.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unable to read class-conditional artifacts: {exc}"]
    errors: list[str] = []
    if metadata.get("window_seconds") != 60:
        errors.append("class-conditional policy must use 60-second features")
    if tuple(metadata.get("feature_names", [])) != FEATURE_NAMES:
        errors.append("class-conditional feature schema changed")
    if metadata.get("calibration", {}).get("sealed_unknown_rows") != 0:
        errors.append("development unknowns leaked into calibration")
    if len(metadata.get("class_support_limits", {})) != 9:
        errors.append("class support limits are incomplete")
    development = metrics.get("development_unknown", {})
    if development.get("status") != "development_unknown_not_fresh_holdout":
        errors.append("development-unknown evaluation status is missing")
    for name in ("known_test_predictions.json", "development_unknown_predictions.json"):
        if not (root / name).is_file():
            errors.append(f"prediction artifact is missing: {name}")
    return errors
