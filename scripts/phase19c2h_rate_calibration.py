#!/usr/bin/env python3
"""Phase 19C.2H: opt-in, fixed-rate healthy calibration (2.5 RPS only).

Default --dry-run does not access Docker/network or write files.
--execute generates genuinely new research API observations; existing artifacts remain unchanged.
No model inference, fault injection, training, transfer API or protected labels.
Rate trial is diagnostic only and does not approve any dataset collection rates.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVICE_NAMES = (
    "postgres", "gateway", "auth-service", "transaction-service",
    "payment-service", "account-service", "ledger-service",
)
REQUEST_FIELDS = (
    "run_id", "request_id", "trace_id", "started_at", "latency_ms",
    "status_code", "expected_outcome", "transport_error", "scenario",
)
EVENT_FIELDS = (
    "run_id", "request_id", "trace_id", "timestamp", "service", "event_type",
    "path", "downstream_service", "latency_ms", "success", "level",
    "status_code", "error_type",
)
DURATION = 75.0


def safe_url(raw: str) -> str:
    url = urlsplit(raw)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost")
            or url.port != 18110 or url.path not in ("", "/")
            or url.query or url.fragment or url.username or url.password):
        raise ValueError("Only the local research gateway http://127.0.0.1:18110 is allowed")
    return "http://127.0.0.1:18110"


def checked_rps(raw: float) -> float:
    """This separately reviewed collector permits *only* one 2.5 RPS trial.

    Never expand its scope by adjusting the original Phase 19C.2C guard.
    """
    if not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw != 2.5:
        raise ValueError("Phase 19C.2H permits exactly 2.5 RPS, diagnostic only")
    return 2.5


def _run_cmd(*args: str) -> str:
    done = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(f"Docker preflight failed: {' '.join(args[:3])}: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def require_clean_local_stack() -> None:
    fault_path = ROOT / "data" / "fault-control" / "active_fault.json"
    if fault_path.exists():
        raise RuntimeError("STOP: existing fault-control file; never remove someone else's fault")
    for service in SERVICE_NAMES:
        container = _run_cmd("docker", "compose", "ps", "-q", service).strip()
        if not container or len(container.splitlines()) != 1:
            raise RuntimeError(f"STOP: service missing/ambiguous: {service}")
        state = _run_cmd("docker", "inspect", "--format", "{{.State.Health.Status}}", container)
        if state != "healthy":
            raise RuntimeError(f"STOP: {service} health status is {state!r}")


def proven_client_rows(raw: list[dict], run_id: str) -> list[dict]:
    """Tag *newly issued* requests using the run ID actually sent in HTTP headers.

    Do not apply this function to historical v1 requests: those have no provable
    outgoing run-header linkage. The caller must be experiments.runner.execute_plan.
    """
    records: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if item.get("scenario") != "valid":
            raise ValueError("Healthy pilot must use only valid research requests")
        rid = item.get("request_id")
        if not isinstance(rid, str) or not rid.startswith(run_id + "-request-") or rid in seen:
            raise ValueError("Request identity does not match newly issued run namespace")
        if not item.get("trace_id") or not item.get("started_at"):
            raise ValueError("Missing client request provenance")
        seen.add(rid)
        row = {field: item.get(field) for field in REQUEST_FIELDS if field != "run_id"}
        records.append({"run_id": run_id, **row})
    if not records:
        raise ValueError("No client observations returned")
    return records


def visible_events(raw: list[dict], run_id: str) -> list[dict]:
    """Allowlist structured fields only; never write tokens or arbitrary log payloads."""
    if any(not isinstance(x, dict) or x.get("run_id") != run_id for x in raw):
        raise ValueError("Mixed-run or malformed structured log records")
    return [{k: item.get(k) for k in EVENT_FIELDS if k in item} for item in raw]


def validate_correlations(requests: list[dict], events: list[dict]) -> None:
    if not requests or not events:
        raise ValueError("Missing client or structured event observations")
    by_id: dict[str, list[dict]] = {}
    for event in events:
        by_id.setdefault(str(event.get("request_id")), []).append(event)
    for request in requests:
        matched = by_id.get(request["request_id"], [])
        if not matched or any(e.get("trace_id") != request["trace_id"] for e in matched):
            raise ValueError(f"Missing or mismatched correlated evidence: {request['request_id']}")
    if any(r["expected_outcome"] is not True or r["status_code"] != 200
           or r["transport_error"] is not None for r in requests):
        raise ValueError("Healthy pilot failed: non-success client observation")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def jsonl_bytes(rows: list[dict]) -> bytes:
    return b"".join((json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8") for row in rows)


def persist_new_pilot(run_id: str, requests: list[dict], events: list[dict],
                      window: object, vector: dict, meta: dict) -> Path:
    """Write immutable, create-only artifacts under v2/calibration/<run_id>."""
    from datasets.final_features import FEATURE_NAMES
    out = ROOT / "data" / "datasets" / "v2" / "calibration" / run_id
    if out.exists():
        raise RuntimeError("STOP: pilot destination already exists")
    contents = {
        "requests.jsonl": jsonl_bytes(requests),
        "events.jsonl": jsonl_bytes(events),
    }
    data_row = {
        "schema_version": "19c2h.calibration.1",
        "run_id": run_id,
        "split": "diagnostic_only",
        "training_eligible": False,
        "threshold_tuning_eligible": False,
        "label": None,
        "candidate_status": "HEALTHY_RATE_CALIBRATION_ONLY",
        "feature_count": len(vector),
        "window_started_at": window.started_at.isoformat(),
        "window_ended_at": window.ended_at.isoformat(),
        "window_seconds": 60,
        "features": vector,
    }
    contents["candidate_feature.json"] = json_bytes(data_row)
    manifest = {
        "schema_version": "19c2h.calibration.1",
        "run_id": run_id,
        "intent": "isolated research-only healthy 2.5 RPS calibration",
        "training_eligible": False,
        "threshold_tuning_eligible": False,
        "split": "diagnostic_only",
        "known_fault_label": None,
        "outgoing_header": "X-Run-ID",
        "run_id_provenance": "new requests generated by experiments.runner.execute_plan",
        "feature_schema_sha256": _sha("\n".join(FEATURE_NAMES).encode()),
        "window_started_at": window.started_at.isoformat(),
        "window_ended_at": window.ended_at.isoformat(),
        "source": "new live localhost research traffic; not a historical v1 upgrade",
        "metadata": meta,
        "artifacts": {name: {"sha256": _sha(data), "bytes": len(data)} for name, data in contents.items()},
    }
    contents["manifest.json"] = json_bytes(manifest)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(exist_ok=False)
    # Exclusive writes: no existing evidence can be replaced.
    for name, data in contents.items():
        with (out / name).open("xb") as handle:
            handle.write(data)
    return out


async def run_pilot(rps: float, gateway_url: str) -> Path:
    from datasets.phase19c2b_exact_windows import (
        V2WindowContractError, build_observable_features, select_exact_window,
    )
    from experiments.runner import PlannedRequest, collect_run_events, execute_plan

    require_clean_local_stack()
    run_id = "v2cal-" + uuid4().hex[:16]
    count = int(round(DURATION * rps))
    plan = [PlannedRequest(
        sequence=i + 1, offset_seconds=i / rps, scenario="valid",
        amount_minor=1000 + i % 1000,
    ) for i in range(count)]
    start = datetime.now(UTC)
    results = await execute_plan(plan, gateway_url, run_id, 1902, 16)
    finish = datetime.now(UTC)
    requests = proven_client_rows(results, run_id)
    events = visible_events(collect_run_events(run_id), run_id)
    validate_correlations(requests, events)
    watermark = max(datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) for e in events)
    try:
        window = select_exact_window(
            phase_started_at=start, phase_ended_at=finish,
            telemetry_watermark=watermark, fault_required=False,
            lead_guard_seconds=2.0, tail_guard_seconds=2.0,
        )
        vector = build_observable_features(
            run_id=run_id, window=window, requests=requests, events=events,
            require_boundary_evidence=True,
        )
    except V2WindowContractError as exc:
        raise RuntimeError(f"INVALID V2 WINDOW (nothing saved): {exc}") from exc
    # Second stack/fault gate, after collection but before persistence.
    require_clean_local_stack()
    meta = {
        "phase_started_at": start.isoformat(),
        "phase_ended_at": finish.isoformat(),
        "telemetry_watermark": watermark.isoformat(),
        "scheduled_rps": rps, "scheduled_requests": count,
        "observed_requests": len(requests), "observed_events": len(events),
        "feature_schema_version": "2.0-candidate-unchanged-65",
        "calibration_phase": "19C.2H", "rate_approved_for_training": False,
        "passed_pre_and_post_run_healthy_no_fault": True,
    }
    out = persist_new_pilot(run_id, requests, events, window, vector, meta)
    print("VALID CALIBRATION CANDIDATE — exact 60s, 65 features; NOT training data")
    print("RUN ID:", run_id)
    print("CLIENT OBSERVATIONS:", len(requests), "SERVICE EVENTS:", len(events))
    print("WINDOW:", window.started_at.isoformat(), "->", window.ended_at.isoformat())
    print("EXCLUSIVE OUTPUT:", out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--rps", type=float, default=2.5)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18110")
    args = parser.parse_args(argv)
    rps = checked_rps(args.rps)
    gateway_url = safe_url(args.gateway_url)
    if args.dry_run:
        print("DRY RUN — no Docker/network access, source changes, training or writes")
        print("PLAN: one 75s localhost research calibration at", rps, "RPS; hard cap 2.5")
        print("REQUIRES: seven healthy services, absent fault-control file, exact 60s candidate")
        print("DATA: create-only under data/datasets/v2/calibration; never training-eligible")
        return 0
    asyncio.run(run_pilot(rps, gateway_url))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print("PHASE 19C.2H STOP:", exc, file=sys.stderr)
        raise SystemExit(2)
