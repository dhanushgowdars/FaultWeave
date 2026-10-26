"""Bounded healthy acceptance run through the isolated V2 research observer.

Requires a separately running observer on localhost:28120 and explicit opt-in.
Generates 113 simulated isolated transactions over 75 seconds. The observer,
not this driver, ingests telemetry and requests frozen Candidate 04 inference.
No fault injection, original gateway write, model/data mutation, or DB reset.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime" / "v2-live-observer-pilot"
OBS = "http://127.0.0.1:28120"
RUN_RE = re.compile(r"v2observer-[0-9a-f]{16}\Z")


def fail(message: str) -> None:
    raise RuntimeError("STOP: " + message)


def observer_status() -> dict:
    request = Request(OBS + "/api/v2/observer/status", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=3) as response:
            if response.status != 200:
                fail("observer status is not ready")
            payload = json.load(response)
    except (OSError, URLError, HTTPError, ValueError) as error:
        fail("isolated research observer is unavailable: " + type(error).__name__)
    if (not isinstance(payload, dict)
            or payload.get("status") != "OBSERVING_ISOLATED_RESEARCH_ONLY"
            or not isinstance(payload.get("run_id"), str)
            or not RUN_RE.fullmatch(payload["run_id"])
            or payload.get("generates_traffic") is not False
            or payload.get("fault_control_enabled") is not False):
        fail("observer identity or safety contract differs")
    return payload


def preflight() -> dict:
    from scripts import faultweave_v2_isolated_db as isolated
    from scripts.faultweave_v2_isolated_unknown_capture import validate_primary_stack
    from scripts.faultweave_v2_candidate04_shadow_watch import check_contract

    state = observer_status()
    if state.get("observed_client_requests") != 0 or state.get("ingested_client_requests") != 0:
        fail("research observer already saw traffic; do not mix pilot runs")
    check_contract()
    isolated.inspect_healthy()
    validate_primary_stack()
    for path in (isolated.FIXTURE / "fault-control/active_fault.json",
                 ROOT / "data/fault-control/active_fault.json"):
        if path.exists() or path.is_symlink():
            fail("fault lease active; healthy pilot refused")
    print("PASS: frozen model, separate stacks, idle leases, empty observer session")
    print("READ ONLY: no transaction, telemetry POST, inference, or write performed")
    return state


def new_receipts(before: set[str], started: datetime) -> list[dict]:
    from scripts import faultweave_v2_candidate04_shadow_watch as watcher

    rows = []
    for path in watcher.OUT.glob("*.prediction.json"):
        if path.name in before:
            continue
        if path.is_symlink() or path.stat().st_size > 65536:
            fail("unsafe new research prediction receipt")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if (watcher.parse_utc(receipt.get("created_at")) < started
                or receipt.get("model_sha256") != watcher.MODEL_SHA256
                or receipt.get("status") != "RESEARCH_ONLY_SHADOW_MONITOR"
                or receipt.get("training_eligible") is not False
                or receipt.get("window_seconds") != 60):
            fail("new research prediction receipt failed provenance validation")
        rows.append({"prediction": receipt.get("prediction"),
                     "window_ended_at": receipt.get("window_ended_at"),
                     "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return sorted(rows, key=lambda row: row["window_ended_at"])


async def run() -> dict:
    from experiments.runner import PlannedRequest, execute_plan
    from scripts import faultweave_v2_candidate04_shadow_watch as watcher
    from scripts.faultweave_v2_isolated_telemetry_bridge import write_new

    initial = preflight()
    run_id = initial["run_id"]
    before = {path.name for path in watcher.OUT.glob("*.prediction.json")}
    started = datetime.now(UTC)
    plan = [PlannedRequest(sequence=i + 1, offset_seconds=i / 1.5,
                           scenario="valid", amount_minor=4000 + i % 100)
            for i in range(113)]
    print("ISOLATED RESEARCH PILOT:", run_id, "113 transactions over ~75s", flush=True)
    rows = await execute_plan(plan, OBS, run_id, 3304, 16)
    if len(rows) != 113 or any(row.get("status_code") != 200 or row.get("expected_outcome") is not True for row in rows):
        fail("some simulated client transactions failed; preserve evidence and stop")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        state = observer_status()
        if state["run_id"] != run_id or state.get("observed_client_requests") != 113:
            fail("observer restarted or received unexpected additional traffic")
        receipts = new_receipts(before, started)
        if state.get("ingested_client_requests") == 113 and receipts:
            if any(item["prediction"] != "NORMAL" for item in receipts):
                fail("healthy pilot had a non-NORMAL frozen prediction; preserve receipts")
            report = {"status": "VERIFIED_HEALTHY_OBSERVER_PILOT", "run_id": run_id,
                      "isolated_transactions": 113, "ingested_client_requests": 113,
                      "prediction_count": len(receipts), "predictions": receipts,
                      "training_eligible": False, "fault_injection": False,
                      "original_gateway_written": False, "created_at": datetime.now(UTC).isoformat()}
            write_new(OUT / (run_id + ".result.json"), report)
            print("PASS: complete isolated client telemetry and", len(receipts), "frozen-model receipt(s)")
            print("CREATE-ONLY REPORT:", OUT / (run_id + ".result.json"))
            return report
    fail("observer did not ingest 113 clients and produce a verified model receipt in time")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--run", action="store_true")
    parser.add_argument("--acknowledge-113-isolated-research-transactions", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight:
        preflight()
    else:
        if not args.acknowledge_113_isolated_research_transactions:
            fail("113 isolated simulated transactions require explicit opt-in")
        asyncio.run(run())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("OBSERVER PILOT STOP:", error)
        raise SystemExit(2)
