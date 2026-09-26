from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import joblib

from experiments.manifest import sha256_file, write_json

from .final_features import FEATURE_NAMES, SERVICE_NAMES
from .final_quality_profile import parse_timestamp, read_json
from .incident_localization import load_manifests
from .isolation_forest_model import anomaly_scores, read_feature_rows

IMPACT_SCHEMA_VERSION = "1.0"
SEVERITY_WEIGHTS = {
    "failure_impact": 0.40,
    "latency_impact": 0.25,
    "affected_service_breadth": 0.20,
    "anomaly_persistence": 0.15,
}
SEVERITY_BANDS = (
    (0.25, "LOW"),
    (0.50, "MEDIUM"),
    (0.75, "HIGH"),
    (1.01, "CRITICAL"),
)


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _feature(row: dict[str, Any], name: str) -> float:
    return float(row["features"].get(name, 0.0))


def severity_band(score: float) -> str:
    clipped = _clip01(score)
    for upper, name in SEVERITY_BANDS:
        if clipped < upper:
            return name
    return "CRITICAL"


def _failure_impact(fault_rows: list[dict[str, Any]]) -> float:
    values = []
    for row in fault_rows:
        values.append(
            max(
                _feature(row, "request_unexpected_outcome_rate"),
                _feature(row, "request_server_error_rate"),
                _feature(row, "request_transport_error_rate"),
                _feature(row, "event_failure_rate"),
                _feature(row, "event_dependency_failure_rate"),
            )
        )
    return _clip01(_mean(values))


def _latency_impact(
    baseline_rows: list[dict[str, Any]], fault_rows: list[dict[str, Any]]
) -> float:
    base_request = _mean([_feature(row, "request_latency_p95_ms") for row in baseline_rows])
    fault_request = _mean([_feature(row, "request_latency_p95_ms") for row in fault_rows])
    base_event = _mean([_feature(row, "event_latency_p95_ms") for row in baseline_rows])
    fault_event = _mean([_feature(row, "event_latency_p95_ms") for row in fault_rows])

    def normalized_ratio(fault: float, baseline: float) -> float:
        if fault <= baseline:
            return 0.0
        ratio = fault / max(baseline, 1.0)
        # 5x baseline p95 saturates the component at 1.0 while smaller increases scale smoothly.
        return _clip01((ratio - 1.0) / 4.0)

    return max(
        normalized_ratio(fault_request, base_request),
        normalized_ratio(fault_event, base_event),
    )


def _affected_service_breadth(
    baseline_rows: list[dict[str, Any]], fault_rows: list[dict[str, Any]]
) -> tuple[float, list[str]]:
    affected: list[str] = []
    for service in SERVICE_NAMES:
        failure_name = f"event_service_{service}_failure_rate"
        latency_name = f"event_service_{service}_latency_p95_ms"
        base_failure = _mean([_feature(row, failure_name) for row in baseline_rows])
        fault_failure = _mean([_feature(row, failure_name) for row in fault_rows])
        base_latency = _mean([_feature(row, latency_name) for row in baseline_rows])
        fault_latency = _mean([_feature(row, latency_name) for row in fault_rows])
        failure_delta = max(0.0, fault_failure - base_failure)
        latency_ratio = fault_latency / max(base_latency, 1.0) if fault_latency > 0 else 0.0
        if failure_delta >= 0.15 or latency_ratio >= 2.0:
            affected.append(service)
    return _clip01(len(affected) / max(1, len(SERVICE_NAMES))), affected


def severity_from_windows(
    baseline_rows: list[dict[str, Any]],
    fault_rows: list[dict[str, Any]],
    anomaly_flags: list[bool],
) -> dict[str, Any]:
    if not baseline_rows or not fault_rows:
        raise ValueError("severity calculation requires baseline and fault windows")
    if len(fault_rows) != len(anomaly_flags):
        raise ValueError("fault windows and anomaly flags are not aligned")

    failure = _failure_impact(fault_rows)
    latency = _latency_impact(baseline_rows, fault_rows)
    breadth, affected = _affected_service_breadth(baseline_rows, fault_rows)
    persistence = _clip01(sum(anomaly_flags) / len(anomaly_flags))
    components = {
        "failure_impact": failure,
        "latency_impact": latency,
        "affected_service_breadth": breadth,
        "anomaly_persistence": persistence,
    }
    score = sum(SEVERITY_WEIGHTS[name] * value for name, value in components.items())
    return {
        "score": round(_clip01(score), 6),
        "level": severity_band(score),
        "components": {name: round(value, 6) for name, value in components.items()},
        "affected_services": affected,
    }


def detection_delay_seconds(
    fault_started_at: datetime,
    fault_rows: list[dict[str, Any]],
    anomaly_flags: list[bool],
) -> float | None:
    if len(fault_rows) != len(anomaly_flags):
        raise ValueError("fault windows and anomaly flags are not aligned")
    detected_at: datetime | None = None
    for row, anomalous in zip(fault_rows, anomaly_flags, strict=True):
        if not anomalous:
            continue
        ended_at = parse_timestamp(row["window_ended_at"])
        if detected_at is None or ended_at < detected_at:
            detected_at = ended_at
    if detected_at is None:
        return None
    return round(max(0.0, (detected_at - fault_started_at).total_seconds()), 6)


def _read_feature_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            item = json.loads(line)
            values = item.get("features")
            if not isinstance(values, dict) or set(values) != set(FEATURE_NAMES):
                raise ValueError("impact feature row does not match frozen feature schema")
            records.append(item)
    if not records:
        raise ValueError("impact feature input is empty")
    return records


def _fault_start(truth: dict[str, Any]) -> datetime:
    for item in truth.get("intervals", []):
        if item.get("name") == "fault":
            return parse_timestamp(item["started_at"])
    raise ValueError("ground truth lacks fault interval")


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 6)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


def _partition_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    detected = [row for row in rows if row["detected"]]
    delays = [float(row["detection_delay_seconds"]) for row in detected]
    return {
        "runs": len(rows),
        "detected_runs": len(detected),
        "detection_rate": round(len(detected) / max(1, len(rows)), 6),
        "median_detection_delay_seconds": round(float(median(delays)), 6) if delays else None,
        "p95_detection_delay_seconds": _percentile(delays, 0.95),
        "severity_counts": dict(sorted(Counter(row["severity"]["level"] for row in rows).items())),
    }


def build_incident_impact_report(
    project_root: Path,
    dataset_root: Path,
    feature_path: Path,
    temporal_model_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    metadata = json.loads((temporal_model_root / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("window_seconds") != 10:
        raise ValueError("Phase 13 temporal detector requires 10-second features")
    if tuple(metadata.get("feature_names", [])) != FEATURE_NAMES:
        raise ValueError("temporal detector feature schema changed")

    model_bundle = joblib.load(temporal_model_root / "model.joblib")
    feature_rows = read_feature_rows(feature_path)
    raw_rows = _read_feature_records(feature_path)
    if len(feature_rows) != len(raw_rows):
        raise ValueError("parsed feature rows are not aligned")
    scores = anomaly_scores(model_bundle["model"], model_bundle["scaler"], feature_rows)
    threshold = float(metadata["threshold"])

    grouped: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for raw, parsed, score in zip(raw_rows, feature_rows, scores, strict=True):
        if str(raw["run_id"]) != parsed.run_id:
            raise ValueError("raw and parsed feature order diverged")
        grouped[parsed.run_id].append((raw, score))

    manifests = [item for item in load_manifests(dataset_root) if item.scenario_type != "normal"]
    results: list[dict[str, Any]] = []
    for manifest in manifests:
        rows = grouped.get(manifest.run_id)
        if not rows:
            raise ValueError(f"{manifest.run_id}: no 10-second feature rows")
        baseline_rows = [raw for raw, _ in rows if raw.get("interval") == "baseline"]
        fault_pairs = [(raw, score) for raw, score in rows if raw.get("interval") == "fault"]
        fault_rows = [raw for raw, _ in fault_pairs]
        anomaly_flags = [score >= threshold for _, score in fault_pairs]
        truth = read_json(project_root / manifest.ground_truth.path)
        fault_started_at = _fault_start(truth)
        delay = detection_delay_seconds(fault_started_at, fault_rows, anomaly_flags)
        severity = severity_from_windows(baseline_rows, fault_rows, anomaly_flags)
        results.append(
            {
                "run_id": manifest.run_id,
                "scenario_type": manifest.scenario_type,
                "split": manifest.split,
                "detected": delay is not None,
                "detection_delay_seconds": delay,
                "anomalous_fault_windows": sum(anomaly_flags),
                "fault_window_count": len(fault_rows),
                "severity": severity,
            }
        )

    partitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        partitions[item["scenario_type"]].append(item)
    report = {
        "schema_version": IMPACT_SCHEMA_VERSION,
        "method": "observable deterministic severity plus 10-second temporal anomaly delay",
        "severity_weights": SEVERITY_WEIGHTS,
        "severity_bands": [
            {"upper_exclusive": upper, "level": level} for upper, level in SEVERITY_BANDS
        ],
        "temporal_detector": {
            "window_seconds": 10,
            "threshold": threshold,
            "training_policy": "healthy training rows only; threshold from eligible known validation rows",
            "development_unknown_tuning_rows": 0,
            "metadata_sha256": sha256_file(temporal_model_root / "metadata.json"),
        },
        "ground_truth_usage": (
            "fault interval start is used only after anomaly scoring to measure offline detection delay; "
            "fault family and expected origin never enter severity or anomaly scoring"
        ),
        "metrics": {
            "overall": _partition_metrics(results),
            "known_fault": _partition_metrics(partitions["known_fault"]),
            "development_unknown": _partition_metrics(partitions["sealed_unknown"]),
        },
        "results": results,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "report.json"
    write_json(path, report)
    report["report_sha256"] = sha256_file(path)
    return report


def validate_incident_impact_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    metrics = report.get("metrics", {})
    if metrics.get("overall", {}).get("runs") != 220:
        errors.append("expected impact results for 220 abnormal runs")
    if metrics.get("known_fault", {}).get("runs") != 180:
        errors.append("expected impact results for 180 known-fault runs")
    if metrics.get("development_unknown", {}).get("runs") != 40:
        errors.append("expected impact results for 40 development-unknown runs")
    temporal = report.get("temporal_detector", {})
    if temporal.get("window_seconds") != 10:
        errors.append("temporal detector must use 10-second windows")
    if temporal.get("development_unknown_tuning_rows") != 0:
        errors.append("development unknowns leaked into temporal tuning")
    if report.get("severity_weights") != SEVERITY_WEIGHTS:
        errors.append("severity weights changed")
    results = report.get("results")
    if not isinstance(results, list) or len(results) != 220:
        errors.append("impact result list is incomplete")
        return errors
    if len({item.get("run_id") for item in results}) != 220:
        errors.append("impact run identities are not unique")
    valid_levels = {level for _, level in SEVERITY_BANDS}
    for item in results:
        severity = item.get("severity", {})
        score = severity.get("score")
        if not isinstance(score, int | float) or not 0.0 <= float(score) <= 1.0:
            errors.append("severity score is outside [0, 1]")
            break
        if severity.get("level") not in valid_levels:
            errors.append("severity level is invalid")
            break
        if item.get("detected") is False and item.get("detection_delay_seconds") is not None:
            errors.append("undetected run must not have a detection delay")
            break
    return errors
