#!/usr/bin/env python3
"""Read-only independent integrity/trace/feature check for Phase19C.2J timeout pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2d_fault_pilot import correlation_gate, parsed_time, recovery_gate  # noqa: E402
from scripts.phase19c2j_timeout_pilot import (  # noqa: E402
    FAULT_ID, INTENSITY, TARGETS, timeout_impact,
)

ARTIFACTS = ("requests.jsonl", "recovery_requests.jsonl", "events.jsonl",
             "candidate_feature.json", "protected_ground_truth.json")
REQUEST_KEYS = {"run_id", "request_id", "trace_id", "started_at", "latency_ms",
                "status_code", "expected_outcome", "transport_error", "scenario"}
EVENT_KEYS = {"run_id", "request_id", "trace_id", "timestamp", "service", "event_type",
              "path", "downstream_service", "latency_ms", "success", "level",
              "status_code", "error_type"}
FORBIDDEN = {"fault_id", "fault_label", "fault_active", "fault_origin"}


def load_json(p: Path) -> dict[str, Any]:
    obj = json.loads(p.read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(obj, dict):
        raise ValueError(f"{p.name}: JSON object required")
    return obj


def load_jsonl(p: Path) -> list[dict]:
    result: list[dict] = []
    with p.open(encoding="utf-8") as fd:
        for no, line in enumerate(fd, 1):
            if line.strip():
                obj = json.loads(line, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                if not isinstance(obj, dict):
                    raise ValueError(f"{p.name} line {no}: not object")
                result.append(obj)
    return result


def audit(root: Path, pilot: Path) -> int:
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import build_observable_features, select_exact_window

    root = root.resolve(strict=True)
    pilot = pilot.resolve(strict=True)
    if pilot.parent != (root / "data/datasets/v2/pilot-fault").resolve() or not pilot.is_dir():
        raise ValueError("Pilot must be directly under data/datasets/v2/pilot-fault")
    manifest = load_json(pilot / "manifest.json")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("v2timeout-") or run_id != pilot.name:
        raise ValueError("Run ID mismatch")
    if (manifest.get("schema_version") != "19c2d.pilot.1" or
        manifest.get("split") != "diagnostic_only" or
        manifest.get("training_eligible") is not False or
        manifest.get("threshold_tuning_eligible") is not False or
        manifest.get("outgoing_header") != "X-Run-ID"):
        raise ValueError("Invalid diagnostic manifest contract")
    expected_schema_hash = hashlib.sha256("\n".join(FEATURE_NAMES).encode()).hexdigest()
    if manifest.get("feature_schema_sha256") != expected_schema_hash:
        raise ValueError("Feature schema/order checksum mismatch")
    for name in ARTIFACTS:
        raw = (pilot / name).read_bytes()
        m = (manifest.get("artifacts") or {}).get(name) or {}
        if m.get("sha256") != hashlib.sha256(raw).hexdigest() or m.get("bytes") != len(raw):
            raise ValueError(f"Checksum/size mismatch: {name}")
    print(f"ARTIFACT CHECKSUMS: {len(ARTIFACTS)}/{len(ARTIFACTS)} verified")
    requests = load_jsonl(pilot / "requests.jsonl")
    recovery = load_jsonl(pilot / "recovery_requests.jsonl")
    events = load_jsonl(pilot / "events.jsonl")
    candidate = load_json(pilot / "candidate_feature.json")
    truth = load_json(pilot / "protected_ground_truth.json")
    if not requests or not recovery or not events:
        raise ValueError("Missing pilot observations")
    if (truth.get("run_id") != run_id or truth.get("fault_id") != FAULT_ID
        or truth.get("target") not in TARGETS or truth.get("intensity") != INTENSITY):
        raise ValueError("Invalid protected ground-truth contract")
    if truth.get("schema_version") != "19c2j.timeout.1":
        raise ValueError("Incorrect protected timeout evidence schema")
    if candidate.get("run_id") != run_id or candidate.get("candidate_status") != "UNLABELED_FAULT_PILOT":
        raise ValueError("Candidate identity/status invalid")
    if (candidate.get("label") is not None or candidate.get("split") != "diagnostic_only" or
        candidate.get("training_eligible") is not False or candidate.get("threshold_tuning_eligible") is not False):
        raise ValueError("Candidate improperly labeled or training-eligible")
    if candidate.get("window_seconds") != 60 or candidate.get("feature_count") != 65:
        raise ValueError("Candidate window/feature count invalid")
    all_requests = [*requests, *recovery]
    if any(set(r) - REQUEST_KEYS or (set(r) & FORBIDDEN) for r in all_requests):
        raise ValueError("Unallowlisted or protected request fields")
    if any(set(e) - EVENT_KEYS or (set(e) & FORBIDDEN) for e in events):
        raise ValueError("Unallowlisted or protected event fields")
    if any(r.get("run_id") != run_id or r.get("scenario") != "valid" for r in all_requests):
        raise ValueError("Mixed-run or invalid client scenario")
    if any(e.get("run_id") != run_id for e in events):
        raise ValueError("Mixed-run event")
    ids = [r.get("request_id") for r in all_requests]
    if len(set(ids)) != len(ids) or any(not isinstance(x, str) or not x.startswith(run_id + "-request-") for x in ids):
        raise ValueError("Duplicate or unproven client request IDs")
    correlation_gate(all_requests, events)
    meta = manifest.get("metadata") or {}
    if meta.get("source_script") != "scripts/phase19c2j_timeout_pilot.py":
        raise ValueError("Unrecognized timeout collector")
    if meta.get("observed_requests") != len(requests) or meta.get("observed_events") != len(events):
        raise ValueError("Manifest raw observation counts differ")
    if meta.get("passed_pre_post_stack_health") is not True:
        raise ValueError("Post-fault stack-health gate not recorded")
    calculated = select_exact_window(
        phase_started_at=meta["phase_started_at"],
        phase_ended_at=meta["phase_ended_at"],
        telemetry_watermark=meta["telemetry_watermark"],
        fault_activated_at=truth["activated_at"],
        fault_expires_at=truth["expires_at"],
        fault_cleared_at=truth["cleared_at"],
        fault_required=True, lead_guard_seconds=2.0, tail_guard_seconds=2.0,
    )
    if (calculated.started_at != parsed_time(candidate["window_started_at"]) or
        calculated.ended_at != parsed_time(candidate["window_ended_at"]) or
        calculated.started_at != parsed_time(manifest["window_started_at"]) or
        calculated.ended_at != parsed_time(manifest["window_ended_at"])):
        raise ValueError("Stored window differs from independently selected fault-only window")
    vector = build_observable_features(run_id=run_id, window=calculated,
                                       requests=requests, events=events,
                                       require_boundary_evidence=True)
    if candidate.get("features") != vector or tuple(vector) != FEATURE_NAMES:
        raise ValueError("Recomputed feature vector does not match saved data")
    impact = timeout_impact(calculated, requests, events, truth["target"])
    recovery_proof = recovery_gate(recovery, events, cleared_at=parsed_time(truth["cleared_at"]))
    if impact != meta.get("impact") or recovery_proof != meta.get("recovery"):
        raise ValueError("Manifest impact/recovery counts conflict with traces")
    print("RUN ID:", run_id)
    print("WINDOW:", calculated.started_at.isoformat(), "->", calculated.ended_at.isoformat())
    print("CORRELATED RESEARCH REQUESTS:", len(all_requests), "total incl. recovery")
    print("OBSERVED FAULT IMPACT:", impact)
    print("POST-CLEAR RECOVERY:", recovery_proof)
    print("FEATURE PARITY: 65/65 identical; protected truth excluded")
    print("VERDICT: VALID_DIAGNOSTIC_TIMEOUT_PILOT — NOT training eligible")
    print("LIMIT: This is one lab pilot, not a v2 training split, real-world performance claim or unknown holdout")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--pilot-dir", type=Path, required=True)
    a = p.parse_args(argv)
    return audit(a.project_root, a.pilot_dir)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, ImportError, TypeError) as exc:
        print("TIMEOUT PILOT AUDIT STOP:", exc, file=sys.stderr)
        raise SystemExit(2)
