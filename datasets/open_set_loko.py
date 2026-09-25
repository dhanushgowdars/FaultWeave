from __future__ import annotations

import json
from itertools import product
from pathlib import Path
from typing import Any

import joblib
from sklearn.metrics import f1_score, precision_score, recall_score
from xgboost import XGBClassifier

from experiments.manifest import sha256_file, write_json

from .isolation_forest_model import anomaly_scores, read_feature_rows
from .open_set_rejection import _class_probabilities, _novelty_distance, _novelty_model, _quantile
from .xgboost_classifier import MODEL_CONFIG, RANDOM_STATE, known_fault_rows, vectors

POLICY_SCHEMA_VERSION = "1.0"
GRID_QUANTILES = tuple(index / 20 for index in range(21))


def _candidate_values(values: list[float]) -> list[float]:
    return sorted({_quantile(values, quantile) for quantile in GRID_QUANTILES})


def _fit_fold_classifier(rows: list[Any], classes: list[str]) -> XGBClassifier:
    model = XGBClassifier(
        objective="multi:softprob",
        num_class=len(classes),
        random_state=RANDOM_STATE,
        n_jobs=-1,
        eval_metric="mlogloss",
        **MODEL_CONFIG,
    )
    model.fit(vectors(rows), [classes.index(row.label) for row in rows])
    return model


def _choose_policy(
    samples: list[dict[str, float | bool]],
) -> tuple[dict[str, float], dict[str, float]]:
    anomaly_scores = [float(item["anomaly_score"]) for item in samples]
    confidences = [float(item["confidence"]) for item in samples]
    distances = [float(item["novelty_distance"]) for item in samples]
    actual_unknown = [bool(item["is_held_out_class"]) for item in samples]
    best: tuple[float, float, float, float, float, float] | None = None
    for anomaly_limit, confidence_limit, novelty_limit in product(
        _candidate_values(anomaly_scores),
        _candidate_values(confidences),
        _candidate_values(distances),
    ):
        predicted_unknown = [
            float(item["anomaly_score"]) >= anomaly_limit
            and (
                float(item["confidence"]) < confidence_limit
                or float(item["novelty_distance"]) > novelty_limit
            )
            for item in samples
        ]
        f1 = float(f1_score(actual_unknown, predicted_unknown, zero_division=0))
        recall = float(recall_score(actual_unknown, predicted_unknown, zero_division=0))
        known_retention = sum(
            not prediction and not actual
            for prediction, actual in zip(predicted_unknown, actual_unknown, strict=True)
        ) / max(1, sum(not actual for actual in actual_unknown))
        candidate = (
            f1,
            recall,
            known_retention,
            -anomaly_limit,
            confidence_limit,
            novelty_limit,
        )
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    predicted_unknown = [
        float(item["anomaly_score"]) >= -best[3]
        and (float(item["confidence"]) < best[4] or float(item["novelty_distance"]) > best[5])
        for item in samples
    ]
    metrics = {
        "synthetic_unknown_f1": round(
            float(f1_score(actual_unknown, predicted_unknown, zero_division=0)), 6
        ),
        "synthetic_unknown_precision": round(
            float(precision_score(actual_unknown, predicted_unknown, zero_division=0)), 6
        ),
        "synthetic_unknown_recall": round(
            float(recall_score(actual_unknown, predicted_unknown, zero_division=0)), 6
        ),
        "known_class_retention": round(best[2], 6),
    }
    return {
        "anomaly_threshold": -best[3],
        "confidence_threshold": best[4],
        "novelty_threshold": best[5],
    }, metrics


def calibrate_loko_policy(
    feature_path: Path,
    isolation_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    rows = read_feature_rows(feature_path)
    training = known_fault_rows(rows, "train")
    validation = known_fault_rows(rows, "validation")
    normal_validation = [
        row
        for row in rows
        if row.split == "validation" and row.scenario_type == "normal" and row.label == "NORMAL"
    ]
    if not normal_validation:
        raise ValueError("healthy validation windows are required for false-positive control")
    classes = sorted({row.label for row in training})
    if len(classes) != 9:
        raise ValueError("leave-one-known-fault-out calibration requires nine known classes")
    isolation_bundle = joblib.load(isolation_root / "model.joblib")
    samples: list[dict[str, float | bool]] = []
    for held_out_class in classes:
        fold_classes = [class_name for class_name in classes if class_name != held_out_class]
        fold_training = [row for row in training if row.label != held_out_class]
        classifier = _fit_fold_classifier(fold_training, fold_classes)
        centroids, scales = _novelty_model(fold_training, fold_classes)
        fold_validation = validation + normal_validation
        probabilities = _class_probabilities(classifier, fold_validation)
        scores = anomaly_scores(
            isolation_bundle["model"],
            isolation_bundle["scaler"],
            fold_validation,
        )
        for row, probability, score in zip(fold_validation, probabilities, scores, strict=True):
            class_index = max(range(len(probability)), key=probability.__getitem__)
            class_name = fold_classes[class_index]
            samples.append(
                {
                    "anomaly_score": score,
                    "confidence": probability[class_index],
                    "novelty_distance": _novelty_distance(row, class_name, centroids, scales),
                    "is_held_out_class": row.label == held_out_class,
                }
            )
    thresholds, metrics = _choose_policy(samples)
    output_root.mkdir(parents=True, exist_ok=True)
    policy = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "calibration_method": "leave_one_known_fault_out",
        "thresholds": thresholds,
        "anomaly_threshold_source": "leave_one_known_fault_out_validation",
        "known_classes": classes,
        "fold_count": len(classes),
        "validation_rows_per_fold": len(validation),
        "healthy_validation_rows_per_fold": len(normal_validation),
        "sealed_unknown_rows": 0,
        "input": {
            "feature_sha256": sha256_file(feature_path),
            "isolation_metadata_sha256": sha256_file(isolation_root / "metadata.json"),
        },
    }
    report = {"schema_version": POLICY_SCHEMA_VERSION, "metrics": metrics}
    write_json(output_root / "policy.json", policy)
    write_json(output_root / "report.json", report)
    return {"policy": policy, "report": report}


def validate_loko_artifacts(root: Path) -> list[str]:
    try:
        policy = json.loads((root / "policy.json").read_text(encoding="utf-8"))
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unable to read leave-one-known-fault-out artifacts: {exc}"]
    errors: list[str] = []
    if policy.get("calibration_method") != "leave_one_known_fault_out":
        errors.append("leave-one-known-fault-out calibration method is missing")
    if policy.get("fold_count") != 9 or len(policy.get("known_classes", [])) != 9:
        errors.append("nine known-fault folds are required")
    if policy.get("sealed_unknown_rows") != 0:
        errors.append("sealed unknown data leaked into leave-one-out calibration")
    if set(policy.get("thresholds", [])) != {
        "anomaly_threshold",
        "confidence_threshold",
        "novelty_threshold",
    }:
        errors.append("open-set thresholds are incomplete")
    if "synthetic_unknown_f1" not in report.get("metrics", {}):
        errors.append("synthetic unknown validation metric is missing")
    return errors
