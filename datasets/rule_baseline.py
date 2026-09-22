from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sklearn.metrics import f1_score, precision_score, recall_score

from experiments.manifest import sha256_file, write_json

from .final_features import FEATURE_NAMES
from .isolation_forest_model import FeatureRow, read_feature_rows

MODEL_SCHEMA_VERSION = "1.0"
WINDOW_SECONDS = 30
RULE_FEATURES = (
    "request_latency_p95_ms",
    "event_latency_p95_ms",
    "request_non_2xx_rate",
    "request_server_error_rate",
    "request_transport_error_rate",
    "event_error_rate",
    "event_failure_rate",
)


def normal_training_rows(rows: list[FeatureRow]) -> list[FeatureRow]:
    selected = [
        row
        for row in rows
        if row.split == "train" and row.scenario_type == "normal" and row.label == "NORMAL"
    ]
    if not selected:
        raise ValueError("no normal training rows available for rule thresholds")
    return selected


def fit_rules(rows: list[FeatureRow]) -> dict[str, float]:
    indexes = {name: FEATURE_NAMES.index(name) for name in RULE_FEATURES}
    # The maximum observed healthy value makes every baseline rule explicit and reproducible.
    return {name: max(row.features[index] for row in rows) for name, index in indexes.items()}


def triggered_rules(row: FeatureRow, rules: dict[str, float]) -> list[str]:
    return [name for name in RULE_FEATURES if row.features[FEATURE_NAMES.index(name)] > rules[name]]


def evaluate(rows: list[FeatureRow], rules: dict[str, float]) -> dict[str, Any]:
    actual = [row.label != "NORMAL" for row in rows]
    predicted = [bool(triggered_rules(row, rules)) for row in rows]
    normal_count = sum(not value for value in actual)
    return {
        "row_count": len(rows),
        "precision": round(float(precision_score(actual, predicted, zero_division=0)), 6),
        "recall": round(float(recall_score(actual, predicted, zero_division=0)), 6),
        "f1": round(float(f1_score(actual, predicted, zero_division=0)), 6),
        "normal_false_positive_rate": round(
            sum(item and not label for item, label in zip(predicted, actual, strict=True))
            / max(1, normal_count),
            6,
        ),
        "rule_trigger_counts": {
            name: sum(name in triggered_rules(row, rules) for row in rows) for name in RULE_FEATURES
        },
    }


def build_rule_baseline(feature_path: Path, output_root: Path, report_path: Path) -> dict[str, Any]:
    rows = read_feature_rows(feature_path)
    train = normal_training_rows(rows)
    rules = fit_rules(train)
    known_test = [
        row for row in rows if row.split == "test" and row.scenario_type != "sealed_unknown"
    ]
    sealed_unknown = [row for row in rows if row.scenario_type == "sealed_unknown"]
    if not known_test or not sealed_unknown:
        raise ValueError("known test and sealed unknown evaluation partitions are required")
    output_root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "model_type": "healthy-envelope rule baseline",
        "window_seconds": WINDOW_SECONDS,
        "feature_names": list(FEATURE_NAMES),
        "rules": rules,
        "training": {
            "rows": len(train),
            "run_count": len({row.run_id for row in train}),
            "sealed_unknown_rows": 0,
        },
        "input": {
            "feature_sha256": sha256_file(feature_path),
            "feature_report_sha256": sha256_file(report_path),
        },
    }
    unknown_trigger_rate = sum(bool(triggered_rules(row, rules)) for row in sealed_unknown) / len(
        sealed_unknown
    )
    metrics = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "known_test": evaluate(known_test, rules),
        "sealed_unknown": {
            "row_count": len(sealed_unknown),
            "trigger_rate": round(unknown_trigger_rate, 6),
            "evaluation_only": True,
        },
    }
    write_json(output_root / "metadata.json", metadata)
    write_json(output_root / "metrics.json", metrics)
    return {"metadata": metadata, "metrics": metrics}


def validate_rule_baseline_artifacts(root: Path) -> list[str]:
    try:
        metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unable to read rule baseline artifacts: {exc}"]
    errors: list[str] = []
    if metadata.get("model_type") != "healthy-envelope rule baseline":
        errors.append("baseline model type is missing")
    if tuple(metadata.get("feature_names", [])) != FEATURE_NAMES:
        errors.append("baseline feature schema changed")
    if set(metadata.get("rules", [])) != set(RULE_FEATURES):
        errors.append("baseline rules are incomplete")
    if metadata.get("training", {}).get("sealed_unknown_rows") != 0:
        errors.append("sealed unknown data leaked into baseline fitting")
    if metrics.get("sealed_unknown", {}).get("evaluation_only") is not True:
        errors.append("sealed unknown evaluation policy is missing")
    return errors
