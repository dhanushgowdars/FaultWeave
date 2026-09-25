from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import joblib

from experiments.manifest import sha256_file, write_json

from .final_features import FEATURE_NAMES
from .isolation_forest_model import FeatureRow, anomaly_scores, read_feature_rows
from .xgboost_classifier import known_fault_rows, vectors

ARTIFACT_SCHEMA_VERSION = "1.0"
OPEN_SET_LABEL = "UNKNOWN ABNORMAL PATTERN"
WINDOW_SECONDS = 30
VALIDATION_ACCEPTANCE_QUANTILE = 0.10


def confidence_threshold(confidences: list[float]) -> float:
    """Choose a known-class acceptance floor from known-fault validation only."""
    return _quantile(confidences, VALIDATION_ACCEPTANCE_QUANTILE)


def _quantile(values: list[float], quantile: float) -> float:
    if not values or not 0.0 <= quantile <= 1.0:
        raise ValueError("a non-empty value list and valid quantile are required")
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * quantile))
    return ordered[index]


def final_label(
    is_anomaly: bool,
    class_name: str,
    confidence: float,
    confidence_limit: float,
    novelty_distance: float,
    novelty_limit: float,
) -> str:
    if not is_anomaly:
        return "NORMAL"
    if confidence < confidence_limit or novelty_distance > novelty_limit:
        return OPEN_SET_LABEL
    return class_name


def _load_artifacts(
    isolation_root: Path, classifier_root: Path
) -> tuple[Any, Any, float, list[str]]:
    isolation_metadata = json.loads((isolation_root / "metadata.json").read_text(encoding="utf-8"))
    classifier_metadata = json.loads(
        (classifier_root / "metadata.json").read_text(encoding="utf-8")
    )
    if tuple(isolation_metadata.get("feature_names", [])) != FEATURE_NAMES:
        raise ValueError("Isolation Forest feature schema does not match frozen features")
    if tuple(classifier_metadata.get("feature_names", [])) != FEATURE_NAMES:
        raise ValueError("XGBoost feature schema does not match frozen features")
    isolation_bundle = joblib.load(isolation_root / "model.joblib")
    classifier_bundle = joblib.load(classifier_root / "model.joblib")
    return (
        isolation_bundle,
        classifier_bundle["model"],
        float(isolation_metadata["threshold"]),
        list(classifier_bundle["classes"]),
    )


def _class_probabilities(model: Any, rows: list[FeatureRow]) -> list[list[float]]:
    return [[float(value) for value in item] for item in model.predict_proba(vectors(rows))]


def _novelty_model(
    rows: list[FeatureRow], classes: list[str]
) -> tuple[dict[str, tuple[float, ...]], tuple[float, ...]]:
    all_vectors = vectors(rows)
    scales: list[float] = []
    for index in range(len(FEATURE_NAMES)):
        mean = sum(row[index] for row in all_vectors) / len(all_vectors)
        variance = sum((row[index] - mean) ** 2 for row in all_vectors) / len(all_vectors)
        scales.append(max(math.sqrt(variance), 1e-9))
    centroids: dict[str, tuple[float, ...]] = {}
    for class_name in classes:
        class_vectors = [row.features for row in rows if row.label == class_name]
        if not class_vectors:
            raise ValueError(f"no known-fault training rows for {class_name}")
        centroids[class_name] = tuple(
            sum(vector[index] for vector in class_vectors) / len(class_vectors)
            for index in range(len(FEATURE_NAMES))
        )
    return centroids, tuple(scales)


def _novelty_distance(
    row: FeatureRow,
    class_name: str,
    centroids: dict[str, tuple[float, ...]],
    scales: tuple[float, ...],
) -> float:
    centroid = centroids[class_name]
    return math.sqrt(
        sum(
            ((value - centroid[index]) / scales[index]) ** 2
            for index, value in enumerate(row.features)
        )
    )


def _predictions(
    rows: list[FeatureRow],
    isolation_bundle: Any,
    classifier: Any,
    anomaly_threshold: float,
    classes: list[str],
    classifier_threshold: float,
    centroids: dict[str, tuple[float, ...]],
    scales: tuple[float, ...],
    novelty_threshold: float,
) -> list[dict[str, Any]]:
    scores = anomaly_scores(isolation_bundle["model"], isolation_bundle["scaler"], rows)
    probabilities = _class_probabilities(classifier, rows)
    records: list[dict[str, Any]] = []
    for row, score, probability in zip(rows, scores, probabilities, strict=True):
        class_index = max(range(len(probability)), key=probability.__getitem__)
        confidence = probability[class_index]
        class_name = classes[class_index]
        novelty_distance = _novelty_distance(row, class_name, centroids, scales)
        predicted = final_label(
            score >= anomaly_threshold,
            class_name,
            confidence,
            classifier_threshold,
            novelty_distance,
            novelty_threshold,
        )
        records.append(
            {
                "run_id": row.run_id,
                "split": row.split,
                "scenario_type": row.scenario_type,
                "interval": row.interval,
                "actual_label": row.label,
                "predicted_label": predicted,
                "anomaly_score": round(score, 8),
                "anomaly_detected": score >= anomaly_threshold,
                "known_class": class_name,
                "known_class_confidence": round(confidence, 8),
                "novelty_distance": round(novelty_distance, 8),
            }
        )
    return records


def _known_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    known = [item for item in records if item["actual_label"] != "NORMAL"]
    normal = [item for item in records if item["actual_label"] == "NORMAL"]
    correct = sum(item["predicted_label"] == item["actual_label"] for item in known)
    rejected = sum(item["predicted_label"] == OPEN_SET_LABEL for item in known)
    normal_correct = sum(item["predicted_label"] == "NORMAL" for item in normal)
    return {
        "row_count": len(records),
        "known_fault_rows": len(known),
        "known_fault_accuracy": round(correct / max(1, len(known)), 6),
        "known_fault_rejection_rate": round(rejected / max(1, len(known)), 6),
        "normal_rows": len(normal),
        "normal_accuracy": round(normal_correct / max(1, len(normal)), 6),
    }


def _sealed_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    fault_rows = [item for item in records if item["actual_label"] != "NORMAL"]
    rejected = sum(item["predicted_label"] == OPEN_SET_LABEL for item in fault_rows)
    return {
        "row_count": len(records),
        "fault_rows": len(fault_rows),
        "unknown_rejection_rate": round(rejected / max(1, len(fault_rows)), 6),
        "predicted_labels": dict(
            sorted(Counter(item["predicted_label"] for item in records).items())
        ),
        "evaluation_only": True,
    }


def build_open_set_rejection(
    feature_path: Path,
    isolation_root: Path,
    classifier_root: Path,
    output_root: Path,
    policy_path: Path | None = None,
) -> dict[str, Any]:
    rows = read_feature_rows(feature_path)
    training = known_fault_rows(rows, "train")
    validation = known_fault_rows(rows, "validation")
    known_test = [
        row for row in rows if row.split == "test" and row.scenario_type != "sealed_unknown"
    ]
    sealed_unknown = [row for row in rows if row.scenario_type == "sealed_unknown"]
    if not known_test or not sealed_unknown:
        raise ValueError("known test and sealed-unknown evaluation partitions are required")
    isolation_bundle, classifier, anomaly_threshold, classes = _load_artifacts(
        isolation_root, classifier_root
    )
    centroids, scales = _novelty_model(training, classes)
    if policy_path is None:
        validation_probabilities = _class_probabilities(classifier, validation)
        acceptance_threshold = confidence_threshold([max(row) for row in validation_probabilities])
        validation_class_names = [
            classes[max(range(len(row)), key=row.__getitem__)] for row in validation_probabilities
        ]
        validation_distances = [
            _novelty_distance(row, class_name, centroids, scales)
            for row, class_name in zip(validation, validation_class_names, strict=True)
        ]
        novelty_threshold = _quantile(validation_distances, 0.95)
        calibration = {
            "source": "known_fault_train_and_validation_only",
            "training_rows": len(training),
            "rows": len(validation),
            "confidence_quantile": VALIDATION_ACCEPTANCE_QUANTILE,
            "novelty_quantile": 0.95,
            "sealed_unknown_rows": 0,
        }
    else:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        if policy.get("calibration_method") != "leave_one_known_fault_out":
            raise ValueError("open-set policy does not use leave-one-known-fault-out calibration")
        thresholds = policy.get("thresholds", {})
        anomaly_threshold = float(thresholds["anomaly_threshold"])
        acceptance_threshold = float(thresholds["confidence_threshold"])
        novelty_threshold = float(thresholds["novelty_threshold"])
        calibration = {
            "source": "leave_one_known_fault_out_validation",
            "policy_sha256": sha256_file(policy_path),
            "fold_count": policy.get("fold_count"),
            "anomaly_threshold_source": "leave_one_known_fault_out_validation",
            "sealed_unknown_rows": 0,
        }
    known_predictions = _predictions(
        known_test,
        isolation_bundle,
        classifier,
        anomaly_threshold,
        classes,
        acceptance_threshold,
        centroids,
        scales,
        novelty_threshold,
    )
    sealed_predictions = _predictions(
        sealed_unknown,
        isolation_bundle,
        classifier,
        anomaly_threshold,
        classes,
        acceptance_threshold,
        centroids,
        scales,
        novelty_threshold,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_type": "open-set anomaly and known-fault gate",
        "window_seconds": WINDOW_SECONDS,
        "feature_names": list(FEATURE_NAMES),
        "open_set_label": OPEN_SET_LABEL,
        "anomaly_threshold": anomaly_threshold,
        "known_class_confidence_threshold": acceptance_threshold,
        "known_class_novelty_threshold": novelty_threshold,
        "confidence_calibration": calibration,
        "inputs": {
            "feature_sha256": sha256_file(feature_path),
            "isolation_metadata_sha256": sha256_file(isolation_root / "metadata.json"),
            "classifier_metadata_sha256": sha256_file(classifier_root / "metadata.json"),
        },
    }
    metrics = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "known_test": _known_summary(known_predictions),
        "sealed_unknown": _sealed_summary(sealed_predictions),
    }
    write_json(output_root / "metadata.json", metadata)
    write_json(output_root / "metrics.json", metrics)
    write_json(output_root / "known_test_predictions.json", known_predictions)
    write_json(output_root / "sealed_unknown_predictions.json", sealed_predictions)
    return {"metadata": metadata, "metrics": metrics}


def validate_open_set_artifacts(root: Path) -> list[str]:
    try:
        metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unable to read open-set artifacts: {exc}"]
    errors: list[str] = []
    if metadata.get("model_type") != "open-set anomaly and known-fault gate":
        errors.append("open-set model type is missing")
    if metadata.get("open_set_label") != OPEN_SET_LABEL:
        errors.append("open-set rejection label is missing")
    if tuple(metadata.get("feature_names", [])) != FEATURE_NAMES:
        errors.append("open-set feature schema changed")
    calibration = metadata.get("confidence_calibration", {})
    if calibration.get("source") not in {
        "known_fault_train_and_validation_only",
        "leave_one_known_fault_out_validation",
    }:
        errors.append("confidence calibration source is invalid")
    if calibration.get("sealed_unknown_rows") != 0:
        errors.append("sealed unknown data leaked into confidence calibration")
    if metrics.get("sealed_unknown", {}).get("evaluation_only") is not True:
        errors.append("sealed unknown evaluation policy is missing")
    for name in ("known_test_predictions.json", "sealed_unknown_predictions.json"):
        if not (root / name).is_file():
            errors.append(f"open-set prediction file is missing: {name}")
    return errors
