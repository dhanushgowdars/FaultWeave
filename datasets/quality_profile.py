from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from experiments.manifest import sha256_file, write_json

from .artifacts import SmokeRunManifest

REQUIRED_REQUEST_FIELDS = frozenset(
    {"request_id", "trace_id", "started_at", "ended_at", "latency_ms", "status_code"}
)
REQUIRED_EVENT_FIELDS = frozenset(
    {"run_id", "request_id", "trace_id", "timestamp", "service", "event_type"}
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"non-object JSONL at {path}:{line_number}")
        records.append(value)
    return records


def missing_fields(records: list[dict[str, Any]], required: frozenset[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records:
        counts.update(field for field in required if field not in record)
    return dict(sorted(counts.items()))


def profile_manifests(project_root: Path, manifests: list[SmokeRunManifest]) -> dict[str, Any]:
    scenario_counts: Counter[str] = Counter()
    fault_counts: Counter[str] = Counter()
    request_missing: Counter[str] = Counter()
    event_missing: Counter[str] = Counter()
    request_count = event_count = correlation_complete_runs = 0
    timings_valid = True
    for manifest in manifests:
        scenario_counts[manifest.scenario_type] += 1
        requests = read_jsonl(project_root / manifest.requests.path)
        events = read_jsonl(project_root / manifest.events.path)
        truth = json.loads((project_root / manifest.ground_truth.path).read_text(encoding="utf-8"))
        request_count += len(requests)
        event_count += len(events)
        request_missing.update(missing_fields(requests, REQUIRED_REQUEST_FIELDS))
        event_missing.update(missing_fields(events, REQUIRED_EVENT_FIELDS))
        if manifest.scenario_type == "known_fault":
            fault_counts[str(truth["fault_id"])] += 1
        timings_valid = timings_valid and all(
            item.get("started_at")
            and item.get("ended_at")
            and item["started_at"] <= item["ended_at"]
            for item in truth.get("intervals", [])
        )
        request_ids = {item["request_id"] for item in requests if item.get("request_id")}
        event_ids = {item["request_id"] for item in events if item.get("request_id")}
        if request_ids and request_ids.issubset(event_ids):
            correlation_complete_runs += 1
    return {
        "schema_version": "1.0",
        "dataset_id": "smoke-dataset-v1",
        "manifest_count": len(manifests),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "known_fault_counts": dict(sorted(fault_counts.items())),
        "request_count": request_count,
        "event_count": event_count,
        "request_missing_fields": dict(sorted(request_missing.items())),
        "event_missing_fields": dict(sorted(event_missing.items())),
        "correlation_complete_runs": correlation_complete_runs,
        "interval_timings_valid": timings_valid,
        "training_eligible": True,
        "sealed_unknown_included": False,
    }


def validate_profile(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report["manifest_count"] != 55:
        errors.append("expected exactly 55 accepted smoke manifests")
    if report["scenario_counts"] != {"known_fault": 45, "normal": 10}:
        errors.append("normal/known-fault class balance is invalid")
    if len(report["known_fault_counts"]) != 9 or any(
        count != 5 for count in report["known_fault_counts"].values()
    ):
        errors.append("known-fault class coverage is invalid")
    if report["request_missing_fields"] or report["event_missing_fields"]:
        errors.append("required observable fields are incomplete")
    if report["correlation_complete_runs"] != 55:
        errors.append("one or more runs lack complete request/event correlation")
    if not report["interval_timings_valid"]:
        errors.append("ground-truth interval timing is invalid")
    if report["sealed_unknown_included"]:
        errors.append("sealed unknown data is forbidden from the smoke dataset")
    return errors


def write_profile(output_path: Path, report: dict[str, Any]) -> str:
    write_json(output_path, report)
    return sha256_file(output_path)
