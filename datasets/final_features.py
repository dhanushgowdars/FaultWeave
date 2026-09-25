from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from experiments.manifest import sha256_file, write_json

from .artifacts import FinalRunManifest
from .final_quality_profile import iter_jsonl, parse_timestamp, percentile, read_json

FEATURE_SCHEMA_VERSION = "2.0"
WINDOW_SECONDS = (10, 30, 60)
SERVICE_NAMES = ("gateway", "authentication", "transaction", "payment", "account", "ledger")
SCENARIO_NAMES = ("valid", "invalid_login", "invalid_account", "invalid_amount")


@dataclass(frozen=True)
class FeatureWindow:
    run_id: str
    split: str
    scenario_type: str
    training_eligible: bool
    threshold_tuning_eligible: bool
    window_seconds: int
    window_index: int
    interval: str
    started_at: datetime
    ended_at: datetime
    label: str
    fault_id: str | None

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


FEATURE_NAMES = (
    "request_count",
    "request_rate_per_second",
    "request_latency_mean_ms",
    "request_latency_p50_ms",
    "request_latency_p95_ms",
    "request_latency_p99_ms",
    "request_latency_max_ms",
    "request_latency_std_ms",
    "request_latency_iqr_ms",
    "request_latency_max_to_p50_ratio",
    "request_success_rate",
    "request_non_2xx_rate",
    "request_client_error_rate",
    "request_server_error_rate",
    "request_transport_error_rate",
    "request_unexpected_outcome_rate",
    "request_valid_rate",
    "request_invalid_login_rate",
    "request_invalid_account_rate",
    "request_invalid_amount_rate",
    "event_count",
    "event_rate_per_second",
    "event_error_rate",
    "event_failure_rate",
    "event_success_rate",
    "event_latency_mean_ms",
    "event_latency_p50_ms",
    "event_latency_p95_ms",
    "event_latency_p99_ms",
    "event_latency_max_ms",
    "event_latency_std_ms",
    "event_latency_iqr_ms",
    "event_latency_max_to_p50_ratio",
    "event_non_2xx_rate",
    "event_error_type_rate",
    "event_timeout_error_rate",
    "event_connection_error_rate",
    "event_dependency_failure_rate",
    "event_downstream_call_rate",
    "event_distinct_service_count",
    "event_distinct_type_count",
    *(f"event_service_{service}_count" for service in SERVICE_NAMES),
    *(f"event_service_{service}_failure_rate" for service in SERVICE_NAMES),
    *(f"event_service_{service}_latency_p95_ms" for service in SERVICE_NAMES),
    *(f"event_downstream_{service}_failure_rate" for service in SERVICE_NAMES),
)

PROTECTED_FIELDS = (
    "run_id",
    "split",
    "scenario_type",
    "training_eligible",
    "threshold_tuning_eligible",
    "interval",
    "label",
    "fault_id",
    "window_started_at",
    "window_ended_at",
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return round(math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)), 3)


def _latency_shape(values: list[float], prefix: str) -> dict[str, float]:
    p25 = float(percentile(values, 0.25) or 0.0)
    p50 = float(percentile(values, 0.50) or 0.0)
    p95 = float(percentile(values, 0.95) or 0.0)
    p99 = float(percentile(values, 0.99) or 0.0)
    maximum = round(max(values), 3) if values else 0.0
    return {
        f"{prefix}_mean_ms": _mean(values),
        f"{prefix}_p50_ms": p50,
        f"{prefix}_p95_ms": p95,
        f"{prefix}_p99_ms": p99,
        f"{prefix}_max_ms": maximum,
        f"{prefix}_std_ms": _std(values),
        f"{prefix}_iqr_ms": round(float(percentile(values, 0.75) or 0.0) - p25, 3),
        f"{prefix}_max_to_p50_ratio": round(maximum / max(p50, 1e-9), 6),
    }


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _windows_from_truth(
    manifest: FinalRunManifest, truth: dict[str, Any], window_seconds: int
) -> list[FeatureWindow]:
    fault_id = truth.get("fault_id")
    windows: list[FeatureWindow] = []
    index = 0
    for item in truth.get("intervals", []):
        interval = str(item["name"])
        started_at = parse_timestamp(item["started_at"])
        ended_at = parse_timestamp(item["ended_at"])
        actual_duration = (ended_at - started_at).total_seconds()
        # The observed interval endpoints can differ from their intended
        # 30/60/30 schedule by a fraction of a second. Split each complete
        # ground-truth interval into its intended number of equal windows so
        # no row crosses an interval boundary or is silently discarded.
        count = {"normal": 120, "baseline": 30, "fault": 60, "recovery": 30}[
            interval
        ] // window_seconds
        if count <= 0:
            continue
        width = timedelta(seconds=actual_duration / count)
        for local_index in range(count):
            cursor = started_at + width * local_index
            end = ended_at if local_index == count - 1 else cursor + width
            windows.append(
                FeatureWindow(
                    run_id=manifest.run_id,
                    split=manifest.split,
                    scenario_type=manifest.scenario_type,
                    training_eligible=manifest.training_eligible,
                    threshold_tuning_eligible=manifest.threshold_tuning_eligible,
                    window_seconds=window_seconds,
                    window_index=index,
                    interval=interval,
                    started_at=cursor,
                    ended_at=end,
                    label=str(fault_id) if interval == "fault" else "NORMAL",
                    fault_id=str(fault_id) if fault_id is not None else None,
                )
            )
            index += 1
    if not windows:
        raise ValueError(f"{manifest.run_id}: no complete {window_seconds}-second windows")
    return windows


def _bucket_records(
    records: Iterable[dict[str, Any]], timestamp_key: str, windows: list[FeatureWindow]
) -> tuple[list[list[dict[str, Any]]], int]:
    buckets = [[] for _ in windows]
    starts = [item.started_at for item in windows]
    assigned = 0
    for record in records:
        timestamp = parse_timestamp(record.get(timestamp_key))
        position = bisect_right(starts, timestamp) - 1
        if position >= 0 and timestamp < windows[position].ended_at:
            buckets[position].append(record)
            assigned += 1
    return buckets, assigned


def _request_features(records: list[dict[str, Any]], duration_seconds: float) -> dict[str, float]:
    count = len(records)
    latencies = [
        value for item in records if (value := _numeric(item.get("latency_ms"))) is not None
    ]
    statuses = [item.get("status_code") for item in records]
    non_2xx = sum(status is None or not 200 <= int(status) < 300 for status in statuses)
    scenarios = Counter(str(item.get("scenario")) for item in records)
    latency_shape = _latency_shape(latencies, "request_latency")
    result = {
        "request_count": float(count),
        "request_rate_per_second": round(count / duration_seconds, 6),
        **latency_shape,
        "request_success_rate": _rate(
            sum(item.get("expected_outcome") is True for item in records), count
        ),
        "request_non_2xx_rate": _rate(non_2xx, count),
        "request_client_error_rate": _rate(
            sum(status is not None and 400 <= int(status) < 500 for status in statuses), count
        ),
        "request_server_error_rate": _rate(
            sum(status is not None and 500 <= int(status) < 600 for status in statuses), count
        ),
        "request_transport_error_rate": _rate(
            sum(item.get("transport_error") is not None for item in records), count
        ),
        "request_unexpected_outcome_rate": _rate(
            sum(item.get("expected_outcome") is False for item in records), count
        ),
    }
    result.update(
        {f"request_{name}_rate": _rate(scenarios[name], count) for name in SCENARIO_NAMES}
    )
    return result


def _event_features(records: list[dict[str, Any]], duration_seconds: float) -> dict[str, float]:
    count = len(records)
    latencies = [
        value for item in records if (value := _numeric(item.get("latency_ms"))) is not None
    ]
    statuses = [item.get("status_code") for item in records]
    services = Counter(str(item.get("service")) for item in records)
    types = {str(item.get("event_type")) for item in records}
    error_types = [str(item.get("error_type") or "").lower() for item in records]
    service_records = {
        service: [item for item in records if item.get("service") == service]
        for service in SERVICE_NAMES
    }
    downstream_records = {
        service: [item for item in records if item.get("downstream_service") == service]
        for service in SERVICE_NAMES
    }
    latency_shape = _latency_shape(latencies, "event_latency")
    result = {
        "event_count": float(count),
        "event_rate_per_second": round(count / duration_seconds, 6),
        "event_error_rate": _rate(
            sum(item.get("level") in {"ERROR", "CRITICAL"} for item in records), count
        ),
        "event_failure_rate": _rate(sum(item.get("success") is False for item in records), count),
        "event_success_rate": _rate(sum(item.get("success") is True for item in records), count),
        **latency_shape,
        "event_non_2xx_rate": _rate(
            sum(status is not None and not 200 <= int(status) < 300 for status in statuses), count
        ),
        "event_error_type_rate": _rate(sum(bool(value) for value in error_types), count),
        "event_timeout_error_rate": _rate(
            sum("timeout" in value or "timed_out" in value for value in error_types), count
        ),
        "event_connection_error_rate": _rate(
            sum(
                any(token in value for token in ("connect", "network", "socket", "reset"))
                for value in error_types
            ),
            count,
        ),
        "event_dependency_failure_rate": _rate(
            sum(
                item.get("downstream_service") is not None and item.get("success") is False
                for item in records
            ),
            count,
        ),
        "event_downstream_call_rate": _rate(
            sum(item.get("downstream_service") is not None for item in records), count
        ),
        "event_distinct_service_count": float(len(services)),
        "event_distinct_type_count": float(len(types)),
    }
    result.update(
        {f"event_service_{service}_count": float(services[service]) for service in SERVICE_NAMES}
    )
    result.update(
        {
            f"event_service_{service}_failure_rate": _rate(
                sum(item.get("success") is False for item in service_records[service]),
                len(service_records[service]),
            )
            for service in SERVICE_NAMES
        }
    )
    result.update(
        {
            f"event_service_{service}_latency_p95_ms": float(
                percentile(
                    [
                        value
                        for item in service_records[service]
                        if (value := _numeric(item.get("latency_ms"))) is not None
                    ],
                    0.95,
                )
                or 0.0
            )
            for service in SERVICE_NAMES
        }
    )
    result.update(
        {
            f"event_downstream_{service}_failure_rate": _rate(
                sum(item.get("success") is False for item in downstream_records[service]),
                len(downstream_records[service]),
            )
            for service in SERVICE_NAMES
        }
    )
    return result


def feature_record(
    window: FeatureWindow, requests: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any]:
    features = _request_features(requests, window.duration_seconds)
    features.update(_event_features(events, window.duration_seconds))
    if tuple(features) != FEATURE_NAMES:
        raise ValueError("feature schema changed unexpectedly")
    if any(not math.isfinite(value) for value in features.values()):
        raise ValueError(f"{window.run_id}: non-finite feature value")
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "run_id": window.run_id,
        "split": window.split,
        "scenario_type": window.scenario_type,
        "training_eligible": window.training_eligible,
        "threshold_tuning_eligible": window.threshold_tuning_eligible,
        "window_seconds": window.window_seconds,
        "window_index": window.window_index,
        "window_started_at": _iso(window.started_at),
        "window_ended_at": _iso(window.ended_at),
        "interval": window.interval,
        "label": window.label,
        "fault_id": window.fault_id,
        "features": features,
    }


def build_feature_rows(
    project_root: Path, manifests: Iterable[FinalRunManifest], window_seconds: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    assigned_requests = assigned_events = total_requests = total_events = 0
    for manifest in manifests:
        truth = read_json(project_root / manifest.ground_truth.path)
        windows = _windows_from_truth(manifest, truth, window_seconds)
        requests = list(iter_jsonl(project_root / manifest.requests.path))
        events = list(iter_jsonl(project_root / manifest.events.path))
        request_buckets, request_assigned = _bucket_records(requests, "started_at", windows)
        event_buckets, event_assigned = _bucket_records(events, "timestamp", windows)
        rows.extend(
            feature_record(window, request_buckets[index], event_buckets[index])
            for index, window in enumerate(windows)
        )
        assigned_requests += request_assigned
        assigned_events += event_assigned
        total_requests += len(requests)
        total_events += len(events)
    return rows, {
        "total_requests": total_requests,
        "assigned_requests": assigned_requests,
        "total_events": total_events,
        "assigned_events": assigned_events,
    }


def write_feature_dataset(
    output_path: Path, rows: list[dict[str, Any]], metrics: dict[str, int]
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return {
        "path": str(output_path),
        "record_count": len(rows),
        "sha256": sha256_file(output_path),
        **metrics,
    }


def feature_schema() -> dict[str, Any]:
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "protected_fields": list(PROTECTED_FIELDS),
        "window_seconds": list(WINDOW_SECONDS),
        "notes": [
            "Features use only observable request and structured-event fields.",
            "Labels and ground-truth metadata are stored separately from the features object.",
            "Identifiers, split metadata and fault metadata are never model inputs.",
            "Sealed unknown rows are evaluation-only and cannot fit or tune models.",
        ],
    }


def build_final_feature_dataset(
    project_root: Path, manifests: list[FinalRunManifest], output_root: Path
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "dataset_id": "faultweave-final-dataset-v1",
        "manifest_count": len(manifests),
        "outputs": {},
    }
    write_json(output_root / "feature-schema.json", feature_schema())
    for seconds in WINDOW_SECONDS:
        rows, metrics = build_feature_rows(project_root, manifests, seconds)
        report["outputs"][str(seconds)] = write_feature_dataset(
            output_root / f"windows-{seconds}s.jsonl", rows, metrics
        )
    write_json(output_root / "report.json", report)
    report["report_sha256"] = sha256_file(output_root / "report.json")
    return report


def validate_feature_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("manifest_count") != 280:
        errors.append("expected features from exactly 280 accepted runs")
    outputs = report.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != {"10", "30", "60"}:
        errors.append("expected 10, 30 and 60-second feature outputs")
        return errors
    # 60-second windows cannot cross the 30/60/30 baseline-fault-recovery
    # boundaries. Each fault run therefore contributes its one complete fault
    # window, while a normal 120-second run contributes two windows.
    for seconds, expected in {"10": 3360, "30": 1120, "60": 340}.items():
        output = outputs[seconds]
        if output.get("record_count") != expected:
            errors.append(f"{seconds}s output expected {expected} windows")
        requests, events = int(output.get("total_requests", 0)), int(output.get("total_events", 0))
        if requests <= 0 or events <= 0:
            errors.append(f"{seconds}s output has no source observations")
        if (
            seconds != "60"
            and requests
            and int(output.get("assigned_requests", 0)) / requests < 0.999
        ):
            errors.append(f"{seconds}s request interval assignment is below 99.9%")
        if seconds != "60" and events and int(output.get("assigned_events", 0)) / events < 0.99:
            errors.append(f"{seconds}s event interval assignment is below 99%")
    return errors
