from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from experiments.manifest import sha256_file, write_json

from .artifacts import FinalRunManifest

REQUIRED_REQUEST_FIELDS = frozenset(
    {
        "request_id",
        "trace_id",
        "started_at",
        "ended_at",
        "latency_ms",
        "status_code",
        "expected_outcome",
        "transport_error",
    }
)
REQUIRED_EVENT_FIELDS = frozenset(
    {"run_id", "request_id", "trace_id", "timestamp", "service", "event_type"}
)
OPTIONAL_EVENT_FEATURES = (
    "downstream_service",
    "error_type",
    "latency_ms",
    "method",
    "path",
    "payment_id",
    "status_code",
    "success",
    "transaction_id",
    "user_id",
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"non-object JSONL at {path}:{line_number}")
            yield value


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"invalid timestamp {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone: {value!r}")
    return parsed.astimezone(UTC)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def interval_windows(truth: dict[str, Any]) -> list[tuple[str, datetime, datetime]]:
    windows: list[tuple[str, datetime, datetime]] = []
    for item in truth.get("intervals", []):
        name = item.get("name")
        if not isinstance(name, str):
            raise ValueError("ground-truth interval lacks a name")
        start = parse_timestamp(item.get("started_at"))
        end = parse_timestamp(item.get("ended_at"))
        if end < start:
            raise ValueError(f"ground-truth interval {name!r} ends before it starts")
        windows.append((name, start, end))
    return windows


def locate_interval(
    timestamp: datetime, windows: list[tuple[str, datetime, datetime]]
) -> str | None:
    for name, start, end in windows:
        if start <= timestamp <= end:
            return name
    return None


def request_interval_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [
        float(item["latency_ms"])
        for item in records
        if item.get("latency_ms") is not None
    ]
    statuses = Counter(
        "null" if item.get("status_code") is None else str(item["status_code"])
        for item in records
    )
    transport_errors = sum(item.get("transport_error") is not None for item in records)
    non_2xx = sum(
        item.get("status_code") is None
        or not 200 <= int(item["status_code"]) < 300
        for item in records
    )
    unexpected = sum(not bool(item.get("expected_outcome")) for item in records)
    count = len(records)
    return {
        "request_count": count,
        "status_counts": dict(sorted(statuses.items())),
        "non_2xx_rate": round(non_2xx / count, 6) if count else None,
        "transport_error_rate": round(transport_errors / count, 6) if count else None,
        "unexpected_outcome_rate": round(unexpected / count, 6) if count else None,
        "latency_ms": {
            "minimum": round(min(latencies), 3) if latencies else None,
            "mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "maximum": round(max(latencies), 3) if latencies else None,
        },
    }


def measurable_fault_signal(intervals: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    baseline = intervals.get("baseline")
    fault = intervals.get("fault")
    if not baseline or not fault:
        return False, ["missing baseline or fault interval"]
    reasons: list[str] = []
    baseline_non_2xx = float(baseline["non_2xx_rate"] or 0)
    fault_non_2xx = float(fault["non_2xx_rate"] or 0)
    if fault_non_2xx >= baseline_non_2xx + 0.02:
        reasons.append("non_2xx_rate_increased")
    baseline_transport = float(baseline["transport_error_rate"] or 0)
    fault_transport = float(fault["transport_error_rate"] or 0)
    if fault_transport >= baseline_transport + 0.01:
        reasons.append("transport_error_rate_increased")
    baseline_p95 = baseline["latency_ms"]["p95"]
    fault_p95 = fault["latency_ms"]["p95"]
    if (
        baseline_p95 is not None
        and fault_p95 is not None
        and fault_p95 >= baseline_p95 * 1.20
        and fault_p95 >= baseline_p95 + 50.0
    ):
        reasons.append("p95_latency_increased")
    baseline_unexpected = float(baseline["unexpected_outcome_rate"] or 0)
    fault_unexpected = float(fault["unexpected_outcome_rate"] or 0)
    if fault_unexpected >= baseline_unexpected + 0.01:
        reasons.append("unexpected_outcome_rate_increased")
    return bool(reasons), reasons


def recovery_is_healthy(intervals: dict[str, dict[str, Any]]) -> bool:
    recovery = intervals.get("recovery")
    if not recovery or not recovery["request_count"]:
        return False
    return (
        float(recovery["transport_error_rate"] or 0) == 0
        and float(recovery["unexpected_outcome_rate"] or 0) == 0
        and float(recovery["non_2xx_rate"] or 0) == 0
    )


def profile_final_manifests(
    project_root: Path,
    manifests: list[FinalRunManifest],
) -> dict[str, Any]:
    scenario_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    known_fault_counts: Counter[str] = Counter()
    sealed_unknown_counts: Counter[str] = Counter()
    request_missing: Counter[str] = Counter()
    event_missing: Counter[str] = Counter()
    optional_by_event_type: dict[str, Counter[str]] = defaultdict(Counter)
    event_type_counts: Counter[str] = Counter()
    service_counts: Counter[str] = Counter()
    request_statuses: Counter[str] = Counter()
    request_scenarios: Counter[str] = Counter()
    transport_types: Counter[str] = Counter()
    request_count = 0
    event_count = 0
    correlated_runs = 0
    interval_assigned_requests = 0
    interval_total_requests = 0
    interval_assigned_events = 0
    interval_total_events = 0
    signal_runs = 0
    recovery_healthy_runs = 0
    run_profiles: list[dict[str, Any]] = []

    for manifest in manifests:
        scenario_counts[manifest.scenario_type] += 1
        split_counts[manifest.split] += 1
        truth = read_json(project_root / manifest.ground_truth.path)
        fault_id = truth.get("fault_id")
        if manifest.scenario_type == "known_fault":
            known_fault_counts[str(fault_id)] += 1
        elif manifest.scenario_type == "sealed_unknown":
            sealed_unknown_counts[str(fault_id)] += 1
        windows = interval_windows(truth)
        request_intervals: dict[str, list[dict[str, Any]]] = defaultdict(list)
        request_ids: set[str] = set()
        request_path = project_root / manifest.requests.path
        for request in iter_jsonl(request_path):
            request_count += 1
            interval_total_requests += 1
            request_missing.update(
                field for field in REQUIRED_REQUEST_FIELDS if field not in request
            )
            request_id = request.get("request_id")
            if request_id:
                request_ids.add(str(request_id))
            request_statuses[
                "null" if request.get("status_code") is None else str(request["status_code"])
            ] += 1
            request_scenarios[str(request.get("scenario"))] += 1
            if request.get("transport_error") is not None:
                transport_types[str(request["transport_error"])] += 1
            try:
                interval = locate_interval(parse_timestamp(request.get("started_at")), windows)
            except ValueError:
                interval = None
            if interval is not None:
                interval_assigned_requests += 1
                request_intervals[interval].append(request)

        event_ids: set[str] = set()
        event_path = project_root / manifest.events.path
        for event in iter_jsonl(event_path):
            event_count += 1
            interval_total_events += 1
            event_missing.update(field for field in REQUIRED_EVENT_FIELDS if field not in event)
            event_id = event.get("request_id")
            if event_id:
                event_ids.add(str(event_id))
            event_type = str(event.get("event_type"))
            service = str(event.get("service"))
            event_type_counts[event_type] += 1
            service_counts[service] += 1
            optional_by_event_type[event_type]["records"] += 1
            optional_by_event_type[event_type].update(
                field for field in OPTIONAL_EVENT_FEATURES if event.get(field) is None
            )
            try:
                interval = locate_interval(parse_timestamp(event.get("timestamp")), windows)
            except ValueError:
                interval = None
            if interval is not None:
                interval_assigned_events += 1
        if request_ids and request_ids.issubset(event_ids):
            correlated_runs += 1

        metrics = {
            name: request_interval_metrics(records)
            for name, records in sorted(request_intervals.items())
        }
        signal_detected = manifest.scenario_type == "normal"
        signal_reasons: list[str] = []
        healthy_recovery = manifest.scenario_type == "normal"
        if manifest.scenario_type != "normal":
            signal_detected, signal_reasons = measurable_fault_signal(metrics)
            healthy_recovery = recovery_is_healthy(metrics)
            signal_runs += int(signal_detected)
            recovery_healthy_runs += int(healthy_recovery)
        run_profiles.append(
            {
                "run_id": manifest.run_id,
                "scenario_type": manifest.scenario_type,
                "split": manifest.split,
                "fault_id": fault_id,
                "target": truth.get("target"),
                "intensity": truth.get("intensity"),
                "interval_metrics": metrics,
                "signal_detected": signal_detected,
                "signal_reasons": signal_reasons,
                "recovery_healthy": healthy_recovery,
                "correlation_complete": bool(request_ids and request_ids.issubset(event_ids)),
            }
        )

    null_profile: dict[str, dict[str, Any]] = {}
    for event_type, counts in sorted(optional_by_event_type.items()):
        records = counts["records"]
        null_profile[event_type] = {
            "records": records,
            "null_counts": {
                field: counts[field] for field in OPTIONAL_EVENT_FEATURES
            },
            "null_rates": {
                field: round(counts[field] / records, 6) for field in OPTIONAL_EVENT_FEATURES
            },
        }

    fault_run_count = scenario_counts["known_fault"] + scenario_counts["sealed_unknown"]
    return {
        "schema_version": "1.0",
        "dataset_id": "faultweave-final-dataset-v1",
        "manifest_count": len(manifests),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "known_fault_counts": dict(sorted(known_fault_counts.items())),
        "sealed_unknown_counts": dict(sorted(sealed_unknown_counts.items())),
        "request_count": request_count,
        "event_count": event_count,
        "request_missing_fields": dict(sorted(request_missing.items())),
        "event_missing_fields": dict(sorted(event_missing.items())),
        "correlation_complete_runs": correlated_runs,
        "request_interval_assignment_rate": round(
            interval_assigned_requests / interval_total_requests, 6
        )
        if interval_total_requests
        else 0,
        "event_interval_assignment_rate": round(
            interval_assigned_events / interval_total_events, 6
        )
        if interval_total_events
        else 0,
        "fault_signal_runs": signal_runs,
        "fault_run_count": fault_run_count,
        "recovery_healthy_runs": recovery_healthy_runs,
        "request_status_counts": dict(sorted(request_statuses.items())),
        "request_scenario_counts": dict(sorted(request_scenarios.items())),
        "transport_error_types": dict(sorted(transport_types.items())),
        "service_counts": dict(sorted(service_counts.items())),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "optional_event_nulls_by_event_type": null_profile,
        "run_profiles": run_profiles,
    }


def validate_final_profile(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report["manifest_count"] != 280:
        errors.append("expected exactly 280 accepted final-dataset manifests")
    if report["scenario_counts"] != {
        "known_fault": 180,
        "normal": 60,
        "sealed_unknown": 40,
    }:
        errors.append("final scenario balance is invalid")
    if report["split_counts"] != {
        "evaluation_only": 40,
        "test": 48,
        "train": 144,
        "validation": 48,
    }:
        errors.append("whole-run split balance is invalid")
    if len(report["known_fault_counts"]) != 9 or any(
        count != 20 for count in report["known_fault_counts"].values()
    ):
        errors.append("known-fault coverage must contain 20 runs for each of 9 classes")
    if len(report["sealed_unknown_counts"]) != 2 or any(
        count != 20 for count in report["sealed_unknown_counts"].values()
    ):
        errors.append("sealed-unknown coverage must contain 20 runs for each of 2 families")
    if report["request_missing_fields"]:
        errors.append("required request fields are missing")
    if report["event_missing_fields"]:
        errors.append("required event fields are missing")
    if report["correlation_complete_runs"] != 280:
        errors.append("one or more runs lack complete request/event correlation")
    if report["request_interval_assignment_rate"] < 0.999:
        errors.append("less than 99.9% of requests map to ground-truth intervals")
    if report["event_interval_assignment_rate"] < 0.99:
        errors.append("less than 99% of events map to ground-truth intervals")
    if report["fault_signal_runs"] != report["fault_run_count"]:
        errors.append("one or more fault runs lack a measurable observable signal")
    if report["recovery_healthy_runs"] != report["fault_run_count"]:
        errors.append("one or more fault runs lack a clean recovery interval")
    if len(report["service_counts"]) < 6:
        errors.append("event data does not cover all six application services")
    if len(report["event_type_counts"]) < 5:
        errors.append("event type diversity is unexpectedly low")
    if report["request_count"] <= 0 or report["event_count"] <= 0:
        errors.append("final dataset contains no observable records")
    return errors


def write_final_profile(output_path: Path, report: dict[str, Any]) -> str:
    write_json(output_path, report)
    return sha256_file(output_path)
