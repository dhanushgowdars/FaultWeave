#!/usr/bin/env python3
"""Phase 19C.2D: single, opt-in, run-scoped known-fault v2 pilot.

Exercises DOWNSTREAM_ERROR_BURST at payment (medium) on the localhost research
transaction path. No service stops, database infrastructure faults, transfer
workflow, model inference, retraining, or automatic labeled-training admission.
The active lease is released in a finally block, including cancellation.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2c_healthy_pilot import (  # noqa: E402
    json_bytes, jsonl_bytes, proven_client_rows, require_clean_local_stack,
    safe_url, visible_events,
)

DURATION_SECONDS = 75
LEASE_SECONDS = 105
RECOVERY_REQUESTS = 8
FAULT_ID = "DOWNSTREAM_ERROR_BURST"
FAULT_TARGET = "payment"
FAULT_INTENSITY = "medium"
OUT_DIR = ROOT / "data" / "datasets" / "v2" / "pilot-fault"


def checked_rps(value: float) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 1.2 <= value <= 2.0:
        raise ValueError("Fault pilot requires 1.2–2.0 RPS; no higher load allowed")
    return float(value)


def parsed_time(value: str | datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(UTC)


def new_plan(run_id: str, rps: float, *, recovery: bool = False):
    from experiments.runner import PlannedRequest
    if recovery:
        first = int(round(DURATION_SECONDS * rps)) + 1
        return [PlannedRequest(sequence=first + i, offset_seconds=i / rps,
                               scenario="valid", amount_minor=3000 + i)
                for i in range(RECOVERY_REQUESTS)]
    count = int(round(DURATION_SECONDS * rps))
    return [PlannedRequest(sequence=i + 1, offset_seconds=i / rps,
                           scenario="valid", amount_minor=2000 + i % 1000)
            for i in range(count)]


async def execute_fault_phase(*, store, run_id: str, plan: list,
                              gateway_url: str, executor):
    """Use the production-exclusive lease; release on every exit, even BaseException."""
    activation = store.acquire(
        run_id=run_id, fault_id=FAULT_ID, target=FAULT_TARGET,
        intensity=FAULT_INTENSITY, duration_seconds=LEASE_SECONDS,
    )
    # This fault is middleware-only. Do NOT run infrastructure-stop adapters.
    start = None
    finish = None
    result = None
    cleared_at = None
    try:
        active = store.active()
        if active is None or active.activation_id != activation.activation_id:
            raise RuntimeError("Fault lease ownership could not be verified")
        start = datetime.now(UTC)
        result = await executor(plan, gateway_url, run_id, 1903, 16)
        finish = datetime.now(UTC)
        if finish >= parsed_time(activation.expires_at):
            raise RuntimeError("Fault lease expired during workload; candidate invalid")
    finally:
        store.release(activation.activation_id)
        cleared_at = datetime.now(UTC)
    return result, start, finish, activation, cleared_at


def correlation_gate(requests: list[dict], events: list[dict]):
    if not requests or not events:
        raise RuntimeError("No run-correlated observations")
    known = {x["request_id"]: x["trace_id"] for x in requests}
    by_id: dict[str, list[dict]] = {}
    for event in events:
        rid = event.get("request_id")
        if rid in known and event.get("trace_id") != known[rid]:
            raise RuntimeError(f"Incorrect service trace for request {rid}")
        by_id.setdefault(str(rid), []).append(event)
    for row in requests:
        if not by_id.get(row["request_id"]):
            raise RuntimeError(f"Missing service events for request {row['request_id']}")


def fault_evidence_gate(window, requests: list[dict], events: list[dict]) -> dict:
    """Only observable payment 500 events, correlated with in-window clients."""
    selected = [row for row in requests if window.started_at < parsed_time(row["started_at"]) <= window.ended_at]
    identities = {r["request_id"]: r["trace_id"] for r in selected}
    impacted = [row for row in selected if row.get("transport_error") is not None
                or row.get("status_code") is None or int(row["status_code"]) >= 500]
    confirmed = [e for e in events if
                 window.started_at < parsed_time(e["timestamp"]) <= window.ended_at
                 and e.get("service") == "payment"
                 and e.get("event_type") == "http_request_completed"
                 and str(e.get("path") or "").startswith("/internal/v1/payments")
                 and e.get("status_code") == 500
                 and e.get("request_id") in identities
                 and e.get("trace_id") == identities[e["request_id"]]]
    if len(selected) < 60:
        raise RuntimeError(f"FAULT_NOT_EVALUABLE: only {len(selected)} in-window client requests")
    if len(impacted) < 10 or len(confirmed) < 5:
        raise RuntimeError(
            "FAULT_NOT_OBSERVABLE: too few correlated client failures or payment 500 events "
            f"(clients={len(impacted)} payment={len(confirmed)})"
        )
    return {"in_window_requests": len(selected), "affected_client_requests": len(impacted),
            "correlated_payment_500_events": len(confirmed)}


def recovery_gate(requests: list[dict], events: list[dict], *, cleared_at: datetime) -> dict:
    if len(requests) != RECOVERY_REQUESTS:
        raise RuntimeError("Incorrect recovery request count")
    if any(parsed_time(row["started_at"]) <= cleared_at for row in requests):
        raise RuntimeError("Recovery request started before fault cleanup")
    if any(row.get("status_code") != 200 or row.get("expected_outcome") is not True
           or row.get("transport_error") is not None for row in requests):
        raise RuntimeError("RECOVERY_NOT_VERIFIED: some same-run requests did not succeed")
    correlation_gate(requests, events)
    return {"successful_requests": len(requests), "same_run_id": True,
            "started_after_fault_clear": True}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def persist(run_id: str, requests: list[dict], recovery: list[dict],
            events: list[dict], window, vector: dict, truth: dict,
            metadata: dict) -> Path:
    from datasets.final_features import FEATURE_NAMES
    out = OUT_DIR / run_id
    if out.exists() or out.is_symlink():
        raise RuntimeError("STOP: create-only pilot directory already exists")
    candidate = {
        "schema_version": "19c2d.pilot.1", "run_id": run_id,
        "split": "diagnostic_only", "training_eligible": False,
        "threshold_tuning_eligible": False, "label": None,
        "candidate_status": "UNLABELED_FAULT_PILOT",
        "feature_count": len(vector), "window_seconds": 60,
        "window_started_at": window.started_at.isoformat(),
        "window_ended_at": window.ended_at.isoformat(), "features": vector,
    }
    assert len(FEATURE_NAMES) == 65 and tuple(vector) == FEATURE_NAMES
    contents = {
        "requests.jsonl": jsonl_bytes(requests),
        "recovery_requests.jsonl": jsonl_bytes(recovery),
        "events.jsonl": jsonl_bytes(events),
        "candidate_feature.json": json_bytes(candidate),
        "protected_ground_truth.json": json_bytes(truth),
    }
    manifest = {
        "schema_version": "19c2d.pilot.1", "run_id": run_id,
        "intent": "run-scoped known-fault research API diagnostic",
        "split": "diagnostic_only", "training_eligible": False,
        "threshold_tuning_eligible": False,
        "outgoing_header": "X-Run-ID", "client_provenance": "experiments.runner.execute_plan",
        "feature_schema_sha256": _sha("\n".join(FEATURE_NAMES).encode()),
        "window_started_at": window.started_at.isoformat(),
        "window_ended_at": window.ended_at.isoformat(),
        "metadata": metadata,
        "artifacts": {name: {"sha256": _sha(data), "bytes": len(data)} for name, data in contents.items()},
    }
    contents["manifest.json"] = json_bytes(manifest)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(exist_ok=False)
    try:
        for name, data in contents.items():
            with (out / name).open("xb") as fd:
                fd.write(data)
    except BaseException:
        # Never silently delete partially written evidence. Preserve for inspection.
        raise
    return out


async def run_pilot(rps: float, gateway_url: str) -> Path:
    from datasets.phase19c2b_exact_windows import (
        V2WindowContractError, build_observable_features, select_exact_window,
    )
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import collect_run_events, execute_plan

    require_clean_local_stack()
    run_id = "v2fault-" + uuid4().hex[:16]
    store = FaultStateStore(ROOT / "data" / "fault-control")
    workload = new_plan(run_id, rps)
    # This call releases the exclusive fault lease even if the workload fails.
    result, start, finish, activation, cleared = await execute_fault_phase(
        store=store, run_id=run_id, plan=workload,
        gateway_url=gateway_url, executor=execute_plan,
    )
    # In particular, never collect recovery data while the fault is active.
    require_clean_local_stack()
    # Run 8 successful control requests with the same run id after fault removal.
    recovery_raw = await execute_plan(new_plan(run_id, rps, recovery=True),
                                      gateway_url, run_id, 1904, 8)
    require_clean_local_stack()
    requests = proven_client_rows(result, run_id)
    recovery = proven_client_rows(recovery_raw, run_id)
    events = visible_events(collect_run_events(run_id), run_id)
    correlation_gate(requests + recovery, events)
    recovery_proof = recovery_gate(recovery, events, cleared_at=cleared)
    watermark = max(parsed_time(e["timestamp"]) for e in events)
    try:
        window = select_exact_window(
            phase_started_at=start, phase_ended_at=finish,
            telemetry_watermark=watermark,
            fault_activated_at=activation.activated_at,
            fault_expires_at=activation.expires_at,
            fault_cleared_at=cleared, fault_required=True,
            lead_guard_seconds=2.0, tail_guard_seconds=2.0,
        )
        vector = build_observable_features(
            run_id=run_id, window=window, requests=requests, events=events,
            require_boundary_evidence=True,
        )
    except V2WindowContractError as exc:
        raise RuntimeError(f"INVALID V2 FAULT WINDOW: {exc}") from exc
    impact = fault_evidence_gate(window, requests, events)
    meta = {
        "phase_started_at": start.isoformat(), "phase_ended_at": finish.isoformat(),
        "telemetry_watermark": watermark.isoformat(),
        "scheduled_rps": rps, "scheduled_requests": len(workload),
        "observed_requests": len(requests), "observed_events": len(events),
        "recovery": recovery_proof, "impact": impact,
        "passed_pre_post_stack_health": True,
        "feature_schema_version": "2.0-candidate-unchanged-65",
    }
    protected = {
        "schema_version": "19c2d.pilot.1", "run_id": run_id,
        "fault_id": FAULT_ID, "target": FAULT_TARGET,
        "intensity": FAULT_INTENSITY,
        "activated_at": parsed_time(activation.activated_at).isoformat(),
        "expires_at": parsed_time(activation.expires_at).isoformat(),
        "cleared_at": cleared.isoformat(),
        "truth_role": "post-observation validity only; prohibited from model features",
    }
    out = persist(run_id, requests, recovery, events, window, vector, protected, meta)
    print("VALID DIAGNOSTIC FAULT PILOT — exact 60s; 65 features; NOT training data")
    print("RUN ID:", run_id)
    print("CLIENT OBSERVATIONS:", len(requests), "SERVICE EVENTS:", len(events))
    print("WINDOW:", window.started_at.isoformat(), "->", window.ended_at.isoformat())
    print("CORRELATED FAULT IMPACT:", impact)
    print("POST-CLEANUP SAME-RUN RECOVERY:", recovery_proof)
    print("EXCLUSIVE OUTPUT:", out)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--execute", action="store_true")
    p.add_argument("--rps", type=float, default=2.0)
    p.add_argument("--gateway-url", default="http://127.0.0.1:18110")
    a = p.parse_args(argv)
    rps = checked_rps(a.rps)
    url = safe_url(a.gateway_url)
    if a.dry_run:
        print("DRY RUN — no Docker, network calls, file writes, or fault activation")
        print(f"PLAN: {DURATION_SECONDS}s at {rps:.2f} RPS via localhost research transactions")
        print(f"FAULT: {FAULT_ID} on {FAULT_TARGET}, intensity={FAULT_INTENSITY}")
        print("EXCLUSIVE LEASE: bounded 105s, always released in finally")
        print("REQUIRES: 7 healthy services, no active fault; 8 successful same-run recovery requests")
        print("DATA: create-only under data/datasets/v2/pilot-fault, diagnostic_only")
        return 0
    asyncio.run(run_pilot(rps, url))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print("PHASE 19C.2D STOP:", exc, file=sys.stderr)
        raise SystemExit(2)
