#!/usr/bin/env python3
"""Offline read-only integrity and feature parity audit for FaultWeave Phase19C.2H calibration.

Requires the original project source and one saved pilot folder. No Docker,
network, database, model inference, training, Git operations, or file writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ARTIFACTS = ("requests.jsonl", "events.jsonl", "candidate_feature.json")
REQUEST_KEYS = {"run_id", "request_id", "trace_id", "started_at", "latency_ms", "status_code", "expected_outcome", "transport_error", "scenario"}
EVENT_KEYS = {"run_id", "request_id", "trace_id", "timestamp", "service", "event_type", "path", "downstream_service", "latency_ms", "success", "level", "status_code", "error_type"}
SERVICE_SET = {"gateway", "authentication", "transaction", "payment", "account", "ledger"}


def iso(s: str) -> datetime:
    v = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if v.tzinfo is None:
        raise ValueError("timezone missing")
    return v.astimezone(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected JSON object")
    return data


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line, n in ((line, n) for n, line in enumerate(f, start=1)):
            if line.strip():
                row = json.loads(line, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
                if not isinstance(row, dict):
                    raise ValueError(f"{path.name}:{n}: expected JSON object")
                rows.append(row)
    return rows


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run(project: Path, pilot_dir: Path) -> int:
    if not pilot_dir.is_dir():
        raise ValueError(f"calibration folder not found: {pilot_dir}")
    project = project.resolve(strict=True)
    pilot_dir = pilot_dir.resolve(strict=True)
    expected_root = (project / "data" / "datasets" / "v2" / "calibration").resolve()
    if pilot_dir.parent != expected_root:
        raise ValueError("calibration folder must be an immediate child of data/datasets/v2/calibration")
    for path in (*ARTIFACTS, "manifest.json"):
        if not (pilot_dir / path).is_file():
            raise ValueError(f"missing {path}")

    m = load_json(pilot_dir / "manifest.json")
    run_id = m.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("v2cal-") or run_id != pilot_dir.name:
        raise ValueError("manifest and directory run IDs differ")
    if m.get("schema_version") != "19c2h.calibration.1":
        raise ValueError("unexpected manifest schema version")
    if (m.get("training_eligible") is not False or m.get("threshold_tuning_eligible") is not False
        or m.get("split") != "diagnostic_only" or m.get("known_fault_label") is not None):
        raise ValueError("calibration accidentally marked eligible or labeled")
    if m.get("outgoing_header") != "X-Run-ID":
        raise ValueError("run-header provenance missing")
    for name in ARTIFACTS:
        raw = (pilot_dir / name).read_bytes()
        entry = (m.get("artifacts") or {}).get(name) or {}
        if sha256(raw) != entry.get("sha256") or len(raw) != entry.get("bytes"):
            raise ValueError(f"integrity check failed: {name}")
    print("ARTIFACT CHECKSUMS: 3/3 verified")

    requests = load_jsonl(pilot_dir / "requests.jsonl")
    events = load_jsonl(pilot_dir / "events.jsonl")
    candidate = load_json(pilot_dir / "candidate_feature.json")
    metadata = m.get("metadata") or {}
    if not requests or not events:
        raise ValueError("requests/events empty")
    if metadata.get("calibration_phase") != "19C.2H" or metadata.get("rate_approved_for_training") is not False:
        raise ValueError("non-diagnostic rate metadata")
    if metadata.get("scheduled_rps") != 2.5 or metadata.get("scheduled_requests") != 188:
        raise ValueError("calibration rate/schedule mismatch")
    if m.get("intent") != "isolated research-only healthy 2.5 RPS calibration":
        raise ValueError("unexpected calibration intent")
    if metadata.get("observed_requests") != len(requests) or metadata.get("observed_events") != len(events):
        raise ValueError("manifest observation counts mismatch")
    if metadata.get("passed_pre_and_post_run_healthy_no_fault") is not True:
        raise ValueError("stack/fault pre/post checks not recorded")
    if any(set(x) - REQUEST_KEYS for x in requests):
        raise ValueError("unallowlisted request field")
    if any(set(x) - EVENT_KEYS for x in events):
        raise ValueError("unallowlisted event field")
    if any(x.get("run_id") != run_id for x in [*requests, *events]):
        raise ValueError("mixed run IDs")
    if any(x.get("scenario") != "valid" or x.get("status_code") != 200
           or x.get("expected_outcome") is not True or x.get("transport_error") is not None
           for x in requests):
        raise ValueError("not all client outcomes are healthy successes")
    seen_ids = set()
    for row in requests:
        rid = row.get("request_id")
        if not isinstance(rid, str) or not rid.startswith(run_id + "-request-") or rid in seen_ids:
            raise ValueError("invalid/duplicate request identity")
        seen_ids.add(rid)
        if not isinstance(row.get("trace_id"), str) or not row["trace_id"]:
            raise ValueError("missing client trace")
        if not isinstance(row.get("latency_ms"),(int,float)) or not math.isfinite(row["latency_ms"]) or row["latency_ms"] < 0:
            raise ValueError("bad client latency")
        iso(row["started_at"])
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_counts: Counter[str] = Counter()
    for row in events:
        iso(row["timestamp"])
        service = row.get("service")
        if service not in SERVICE_SET:
            raise ValueError("unexpected service in log observations")
        event_counts[service] += 1
        by_id[str(row.get("request_id"))].append(row)
    for row in requests:
        related = by_id.get(row["request_id"], [])
        if not related or any(e.get("trace_id") != row["trace_id"] for e in related):
            raise ValueError(f"missing or inconsistent correlation: {row['request_id']}")

    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import ExactWindow, build_observable_features
    schema_digest = sha256("\n".join(FEATURE_NAMES).encode())
    if m.get("feature_schema_sha256") != schema_digest:
        raise ValueError("feature schema digest mismatch")
    if candidate.get("run_id") != run_id or candidate.get("window_seconds") != 60:
        raise ValueError("candidate run or window mismatch")
    if candidate.get("split") != "diagnostic_only" or candidate.get("training_eligible") is not False or candidate.get("threshold_tuning_eligible") is not False:
        raise ValueError("candidate improperly marked for training")
    start = iso(candidate["window_started_at"])
    end = iso(candidate["window_ended_at"])
    if start != iso(m["window_started_at"]) or end != iso(m["window_ended_at"]):
        raise ValueError("manifest/candidate time disagreement")
    phase_start, phase_end = iso(metadata["phase_started_at"]), iso(metadata["phase_ended_at"])
    watermark = iso(metadata["telemetry_watermark"])
    if start < phase_start + timedelta(seconds=2) or end > phase_end - timedelta(seconds=2) or end > watermark:
        raise ValueError("window outside guarded, watermarked phase")
    window = ExactWindow(started_at=start, ended_at=end)
    fresh_features = build_observable_features(run_id=run_id, window=window,
                                                requests=requests, events=events,
                                                require_boundary_evidence=True)
    stored = candidate.get("features")
    if not isinstance(stored, dict) or set(stored) != set(fresh_features):
        raise ValueError("missing/extra feature keys")
    bad = {name:(fresh_features[name],stored.get(name)) for name in fresh_features if stored.get(name) != fresh_features[name]}
    if bad:
        raise ValueError(f"saved feature values differ from recomputation: {list(bad)[:8]}")
    if candidate.get("feature_count") != 65:
        raise ValueError("candidate feature count !=65")
    selected_request_count = sum(start < iso(r["started_at"]) <= end for r in requests)
    print("RUN ID:", run_id)
    print("OBSERVATIONS:", len(requests), "client;", len(events), "service events")
    print("SERVICE EVENT COUNTS:", dict(sorted(event_counts.items())))
    print("CORRELATIONS:", len(requests), "/", len(requests), "requests linked to matching traces")
    print("WINDOW:", start.isoformat(), "->", end.isoformat(), "duration 60s")
    print("IN-WINDOW CLIENT REQUESTS:", selected_request_count)
    print("RECOMPUTED FEATURE VECTOR:", len(fresh_features), "/ 65 identical")
    for field in ("request_count", "request_rate_per_second", "request_success_rate", "event_count", "event_error_rate", "event_failure_rate"):
        print("  ", field, "=", fresh_features.get(field))
    request_lats = sorted(float(row["latency_ms"]) for row in requests if start < iso(row["started_at"]) <= end)
    if request_lats:
        rank = (len(request_lats) - 1) * .95
        low = int(rank)
        high = min(low + 1, len(request_lats) - 1)
        p95 = request_lats[low] + (request_lats[high] - request_lats[low]) * (rank - low)
        print("CLIENT LATENCY P95 (in-window):", round(p95, 3), "ms")
    print("VERDICT: VALID_DIAGNOSTIC_RATE_CALIBRATION — remains NOT training eligible")
    print("LIMIT: one 2.5 RPS trial does not approve collection rates or prove continuous Docker health")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--calibration-dir", type=Path, required=True)
    a = p.parse_args()
    return run(a.project_root, a.calibration_dir)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, ImportError) as e:
        print("CALIBRATION AUDIT STOP:", e, file=sys.stderr)
        raise SystemExit(2)
