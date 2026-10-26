"""Read-only Phase 19B.1 audit of the frozen 60-second feature contract.

Does not load model pickle files, access demo session secrets, mutate the backend,
retrain, or change acceptance evidence. The report is create-only (-x).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

EXPECTED_COUNT = 65
EXPECTED_SERVICES = ("gateway", "authentication", "transaction", "payment", "account", "ledger")
MODEL_SUBDIR = Path("rich-v2-60s")
METADATA_FILES = {
    "isolation": MODEL_SUBDIR / "isolation-forest" / "metadata.json",
    "classifier": MODEL_SUBDIR / "xgboost-known-fault" / "metadata.json",
    "support": MODEL_SUBDIR / "class-conditional-open-set" / "metadata.json",
}
SNAPSHOT_KEYS = ("snapshot", "model_window", "inference_snapshot", "window")


class AuditError(ValueError):
    """The audit cannot establish a trustworthy compatibility result."""


def json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AuditError(f"expected JSON object: {path}")
    return data


def extract_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    """Only follow explicitly named observable snapshot fields, never scenario/truth."""
    if isinstance(document.get("features"), dict):
        return document
    for name in SNAPSHOT_KEYS:
        candidate = document.get(name)
        if isinstance(candidate, dict) and isinstance(candidate.get("features"), dict):
            return candidate
    raise AuditError(
        "saved JSON has no 65-feature inference vector; a result summary alone "
        "cannot reproduce the score. Export the exact pre-inference snapshot "
        "without rerunning a challenge or revealing scenario ground truth."
    )


def frozen_contract(root: Path) -> dict[str, Any]:
    meta = {key: json_object(root / location) for key, location in METADATA_FILES.items()}
    schema = meta["isolation"].get("feature_names")
    if not isinstance(schema, list) or len(schema) != EXPECTED_COUNT or len(set(schema)) != EXPECTED_COUNT:
        raise AuditError("frozen Isolation Forest metadata lacks 65 unique feature names")
    for source, entry in meta.items():
        if entry.get("window_seconds") != 60 or entry.get("feature_names") != schema:
            raise AuditError(f"frozen {source} metadata does not match 60s feature contract")
    services = {name.removeprefix("event_service_").removesuffix("_count")
                for name in schema if name.startswith("event_service_") and name.endswith("_count")}
    if services != set(EXPECTED_SERVICES):
        raise AuditError(f"frozen service namespaces changed: {sorted(services)}")
    for name in EXPECTED_SERVICES:
        if f"event_downstream_{name}_failure_rate" not in schema:
            raise AuditError(f"missing frozen downstream namespace: {name}")
    profiles = meta["support"].get("class_support_profiles")
    limits = meta["support"].get("class_support_limits")
    classes = meta["classifier"].get("classes")
    if not isinstance(classes, list) or not isinstance(profiles, dict) or not isinstance(limits, dict):
        raise AuditError("frozen classifier and support metadata are incomplete")
    if set(classes) != set(profiles) or set(classes) != set(limits):
        raise AuditError("frozen candidate classes and support classes disagree")
    return {"schema": schema, "metadata": meta, "sha256": hashlib.sha256("\n".join(schema).encode()).hexdigest()}


def value_semantics(name: str, value: float) -> str:
    if name.endswith("_rate_per_second"):
        if value < 0:
            raise AuditError(f"negative event/request rate: {name}")
        return "records/second"
    if name.endswith("_rate") and not name.endswith("max_to_p50_ratio"):
        if not 0 <= value <= 1:
            raise AuditError(f"fraction must be in [0,1]: {name}={value}")
        return "fraction [0,1]"
    if name.endswith("_count") or name in ("request_count", "event_count"):
        if value < 0 or not value.is_integer():
            raise AuditError(f"count must be a nonnegative integer: {name}={value}")
        return "raw count"
    if name.endswith("_ms"):
        if value < 0:
            raise AuditError(f"negative millisecond duration: {name}")
        return "milliseconds"
    if name.endswith("_ratio"):
        if value < 0:
            raise AuditError(f"negative ratio: {name}")
        return "dimensionless ratio"
    raise AuditError(f"unknown feature-unit contract: {name}")


def normalize_features(snapshot: dict[str, Any], schema: list[str]) -> tuple[dict[str, float], list[str]]:
    supplied = snapshot["features"]
    missing, extra = sorted(set(schema) - set(supplied)), sorted(set(supplied) - set(schema))
    if missing or extra:
        raise AuditError(f"feature name mismatch: missing={missing} extra={extra}")
    if len(supplied) != EXPECTED_COUNT:
        raise AuditError("snapshot feature count is not 65")
    values: dict[str, float] = {}
    for name in schema:
        raw = supplied[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            raise AuditError(f"missing, nonnumeric, or nonfinite feature: {name}")
        values[name] = float(raw)
        value_semantics(name, values[name])
    warnings: list[str] = []
    if list(supplied) != schema:
        warnings.append("serialized feature mapping order differs; inference must use frozen metadata ordering")
    seconds = snapshot.get("window_seconds")
    if seconds != 60:
        raise AuditError(f"snapshot is not marked as a frozen 60s window: {seconds!r}")
    source = snapshot.get("request_observation_source")
    if source != "client_observed":
        raise AuditError(f"request observation source not verified as client_observed: {source!r}")
    for key in ("request_count", "event_count"):
        if key in snapshot and snapshot[key] != values[key]:
            raise AuditError(f"{key} snapshot metadata and computed feature disagree")
    coverage = snapshot.get("coverage_seconds")
    if coverage is not None and (not isinstance(coverage, (float, int)) or coverage < 60):
        raise AuditError("snapshot reports less than 60 seconds telemetry coverage")
    return values, warnings


def training_statistics(path: Path | None, schema: list[str], candidate: str) -> tuple[dict[str, dict[str, float]], int]:
    if path is None:
        return {}, 0
    series: dict[str, list[float]] = {feature: [] for feature in schema}
    with path.open(encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, 1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise AuditError(f"training row {line_num} is not a JSON object")
            # Training split and ordinary known-fault rows only; never read challenge truth.
            if not (row.get("split") == "train" and row.get("label") == candidate
                    and row.get("scenario_type") == "known_fault"
                    and row.get("interval") == "fault" and row.get("training_eligible") is True
                    and row.get("window_seconds") == 60):
                continue
            sample = row.get("features")
            if not isinstance(sample, dict) or set(sample) != set(schema):
                raise AuditError(f"training row {line_num} has incompatible schema")
            for name in schema:
                raw = sample[name]
                if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                    raise AuditError(f"training row {line_num} invalid feature {name}")
                series[name].append(float(raw))
    count = len(series[schema[0]])
    if count == 0:
        raise AuditError("no fitting-split rows exist for selected candidate in provided training JSONL")
    return {
        name: {"minimum": min(numbers), "median": statistics.median(numbers),
               "mean": statistics.fmean(numbers), "maximum": max(numbers),
               "profile_scale_from_train": max(statistics.pstdev(numbers),
                   abs(statistics.median(numbers)) * 1e-4, 1e-3)}
        for name, numbers in series.items()
    }, count


def audit(snapshot: dict[str, Any], frozen: dict[str, Any], candidate: str,
          stats: dict[str, dict[str, float]] | None = None,
          *, expected_score: float | None = None, expected_limit: float | None = None) -> dict[str, Any]:
    schema = frozen["schema"]
    values, warnings = normalize_features(snapshot, schema)
    metadata = frozen["metadata"]
    profile = metadata["support"]["class_support_profiles"].get(candidate)
    if not isinstance(profile, dict):
        raise AuditError(f"candidate absent from frozen support profiles: {candidate}")
    centers, scales = profile.get("centers"), profile.get("scales")
    if not isinstance(centers, list) or not isinstance(scales, list) or len(centers) != 65 or len(scales) != 65:
        raise AuditError("selected class has malformed 65-element support profile")
    contributors = []
    for name, center, scale in zip(schema, centers, scales, strict=True):
        if not isinstance(center, (int, float)) or not math.isfinite(center):
            raise AuditError(f"invalid support center for {name}")
        if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
            raise AuditError(f"invalid support scale for {name}")
        if stats and name in stats:
            sample = stats[name]
            if not math.isclose(float(center), sample["median"], rel_tol=1e-9, abs_tol=1e-7):
                raise AuditError(f"frozen profile center disagrees with original fitting rows: {name}")
            if not math.isclose(float(scale), sample["profile_scale_from_train"], rel_tol=1e-9, abs_tol=1e-7):
                raise AuditError(f"frozen profile scale disagrees with original fitting rows: {name}")
        distance = abs(values[name] - float(center)) / float(scale)
        contributors.append({"feature": name, "live": values[name], "unit": value_semantics(name, values[name]),
                             "profile_median": float(center), "profile_scale": float(scale),
                             "normalized_distance": distance,
                             "training": (stats or {}).get(name)})
    contributors.sort(key=lambda item: (-item["normalized_distance"], item["feature"]))
    score = contributors[0]["normalized_distance"]
    limit = float(metadata["support"]["class_support_limits"][candidate])
    if not math.isfinite(limit) or limit <= 0:
        raise AuditError("invalid support limit")
    if expected_score is not None and not math.isclose(score, expected_score, rel_tol=1e-8, abs_tol=1e-6):
        raise AuditError(f"support score mismatch: recomputed={score:.8f} saved={expected_score:.8f}")
    if expected_limit is not None and not math.isclose(limit, expected_limit, rel_tol=1e-9, abs_tol=1e-7):
        raise AuditError(f"support limit mismatch: frozen={limit:.8f} saved={expected_limit:.8f}")
    return {
        "audit_version": "19B.1-read-only-v1", "status": "REPRODUCED" if expected_score is not None else "CALCULATED",
        "source": "observable_features_only", "hidden_fault_label_used": False,
        "feature_count": len(schema), "frozen_feature_schema_sha256": frozen["sha256"],
        "candidate": candidate, "support_formula": "max(abs(live_i - class_median_i) / class_scale_i)",
        "support_score": score, "support_limit": limit, "score_over_limit": score / limit,
        "support_decision": "REJECT" if score > limit else "ACCEPT",
        "responsible_features": [v["feature"] for v in contributors if math.isclose(v["normalized_distance"], score, rel_tol=1e-12, abs_tol=1e-9)],
        "features_ranked": contributors, "warnings": warnings,
        "source_semantics": {
            "snapshot_request_source": snapshot.get("request_observation_source"),
            "event_filter_comparison": "NOT_VERIFIED_FROM_AGGREGATE_VECTOR",
            "training_and_live_aggregation_equivalence": "REQUIRES_RAW_REQUEST_AND_EVENT_PROBES",
            "window_phase_isolation": "NOT_VERIFIED_FROM_AGGREGATE_VECTOR",
        },
    }



def _timestamp(value: Any):
    from datetime import UTC, datetime
    if not isinstance(value, str):
        raise AuditError("raw observation is missing an ISO 8601 timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise AuditError("raw observation timestamp has no timezone")
    return result.astimezone(UTC)


def _jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            record = json.loads(line)
            if not isinstance(record, dict):
                raise AuditError(f"raw JSONL item is not an object: {path}:{number}")
            yield record


def raw_input_comparison(snapshot: dict[str, Any], request_path: Path,
                         event_path: Path) -> dict[str, Any]:
    """Recalculate using both existing extractors; never inspect fault truth fields.

    Raw files must represent the original observations ingested for the exact
    saved window. This will flag mismatches but cannot prove upstream completeness.
    """
    start = _timestamp(snapshot.get("window_started_at"))
    end = _timestamp(snapshot.get("window_ended_at"))
    if (end - start).total_seconds() != 60:
        raise AuditError("raw comparison requires an actual 60s snapshot interval")
    root = Path(__file__).resolve().parents[1]
    for location in (root, root / "shared", root / "services" / "gateway"):
        if str(location) not in sys.path:
            sys.path.insert(0, str(location))
    from app.intelligence_live import (  # imported only for raw provenance verification
        eligible_runtime_event, event_features, request_features,
    )
    from datasets.final_features import _event_features, _request_features

    raw_requests = list(_jsonl(request_path))
    raw_events = list(_jsonl(event_path))
    requests = [item for item in raw_requests
                if start < _timestamp(item.get("started_at")) <= end]
    in_window_events = [item for item in raw_events
                        if start < _timestamp(item.get("timestamp")) <= end]
    events = [item for item in in_window_events if eligible_runtime_event(item)]
    live = {**request_features(requests, 60.0), **event_features(events, 60.0)}
    training_aggregation_same_rows = {
        **_request_features(requests, 60.0), **_event_features(events, 60.0),
    }
    different_between_implementations = [
        name for name in live if not math.isclose(
            float(live[name]), float(training_aggregation_same_rows[name]),
            rel_tol=1e-9, abs_tol=1e-6,
        )
    ]
    saved = snapshot["features"]
    different_from_saved = [
        name for name in live if name not in saved or not math.isclose(
            float(live[name]), float(saved[name]), rel_tol=1e-9, abs_tol=1e-6,
        )
    ]
    return {
        "input_file_request_rows": len(raw_requests),
        "input_file_event_rows": len(raw_events),
        "in_window_requests": len(requests),
        "in_window_raw_events": len(in_window_events),
        "in_window_eligible_events": len(events),
        "events_excluded_by_live_filter": len(in_window_events) - len(events),
        "aggregation_feature_mismatches_training_vs_live": different_between_implementations,
        "aggregate_mismatches_vs_saved_snapshot": different_from_saved,
        "status": "MATCH" if not (different_between_implementations or different_from_saved)
                  else "MISMATCH",
        "caveat": "Exact input completeness, deduplication and edge assignment require capture provenance; training used [start,end) but live uses (start,end].",
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Saved observable pre-inference snapshot containing 65 features")
    parser.add_argument("--model-root", type=Path, default=Path("data/datasets/final/models"))
    parser.add_argument("--training-rows", type=Path, help="Original 60s training JSONL, optional but required for min/mean/max")
    parser.add_argument("--requests-jsonl", type=Path, help="Raw client-observed request rows from saved window")
    parser.add_argument("--events-jsonl", type=Path, help="Raw structured event rows before live eligibility filtering")
    parser.add_argument("--candidate", default="DATABASE_HIGH_LATENCY")
    parser.add_argument("--expected-score", type=float, default=4916.66666667)
    parser.add_argument("--expected-limit", type=float, default=10.8173424)
    parser.add_argument("--report", type=Path, help="New non-existing JSON report (never overwritten)")
    args = parser.parse_args()
    try:
        frozen = frozen_contract(args.model_root)
        snapshot = extract_snapshot(json_object(args.snapshot))
        stats, count = training_statistics(args.training_rows, frozen["schema"], args.candidate)
        result = audit(snapshot, frozen, args.candidate, stats,
                       expected_score=args.expected_score, expected_limit=args.expected_limit)
        result["fitting_training_rows"] = count
        if bool(args.requests_jsonl) != bool(args.events_jsonl):
            raise AuditError("raw-source comparison requires BOTH --requests-jsonl and --events-jsonl")
        if args.requests_jsonl:
            result["raw_input_comparison"] = raw_input_comparison(
                snapshot, args.requests_jsonl, args.events_jsonl
            )
            if result["raw_input_comparison"]["status"] != "MATCH":
                raise AuditError("raw request/event reconstruction differs from saved feature vector")
            result["source_semantics"]["training_and_live_aggregation_equivalence"] = (
                "MATCH_ON_PROVIDED_RAW_OBSERVATIONS;_UPSTREAM_COMPLETENESS_NOT_PROVEN"
            )
            result["source_semantics"]["event_filter_comparison"] = (
                "EXCLUDED_EVENT_COUNT_REPORTED;TRAINING_USED_UNFILTERED_LOG_INPUTS"
            )
        if args.report:
            # Exclusive create: neither model files nor an existing report can be replaced.
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
        print(f"PASS: support score reproduced: {result['support_score']:.8f} / limit {result['support_limit']:.8f}")
        print(f"Responsible feature(s): {', '.join(result['responsible_features'])}")
        for row in result["features_ranked"][:10]:
            training = row["training"] or {}
            print(f"  {row['feature']}: live={row['live']:.8g}, median={row['profile_median']:.8g}, "
                  f"scale={row['profile_scale']:.8g}, distance={row['normalized_distance']:.6f}, "
                  f"train_min={training.get('minimum', 'N/A')}, train_max={training.get('maximum', 'N/A')}")
        if not stats:
            print("NOTE: Training min/mean/max unavailable; supply --training-rows to verify.")
        if "raw_input_comparison" not in result:
            print("NOTE: Raw-source filtering unverified: provide --requests-jsonl AND --events-jsonl.")
        print("NOTE: Window-phase isolation unverified by aggregate snapshot alone.")
        return 0
    except (AuditError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"AUDIT BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
