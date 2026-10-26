"""One bounded, research-only SERVICE_UNAVAILABLE acceptance run.

Uses a fresh in-process observer on 127.0.0.1:28120 and the isolated clone's
payment fault control. No training capture, threshold tuning, original-gateway
write, original PostgreSQL change, or automatic retry. A mismatch is recorded
as a failure, not repaired by fitting the frozen model again.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import socket
import threading
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from scripts import faultweave_v2_live_observer as observer
from scripts import faultweave_v2_live_observer_pilot as pilot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime" / "v2-isolated-fault-acceptance"
LABEL, TARGET, INTENSITY = "SERVICE_UNAVAILABLE", "payment", "medium"
COUNT, RECOVERY_COUNT, RATE, LEASE_SECONDS = 113, 8, 1.5, 110


def fail(message: str) -> None:
    raise RuntimeError("STOP: " + message)


def preflight(*, wait_stale: bool = False) -> tuple[str, ...]:
    """Read-only checks; optional wait simply lets prior buffer data age out."""
    from scripts.faultweave_v2_isolated_rolling_feeder import no_recent_data
    from scripts import faultweave_v2_isolated_db as isolated
    from scripts.faultweave_v2_isolated_unknown_capture import validate_primary_stack
    from scripts import faultweave_v2_candidate04_shadow_watch as watcher
    from experiments.faults.catalog import validate_known_fault

    if (observer.OUT / "active.lock").exists() or (observer.OUT / "active.lock").is_symlink():
        fail("old observer is still running or its lock needs review; stop it normally first")
    with socket.socket() as probe:
        try:
            probe.bind((observer.HOST, observer.PORT))
        except OSError as error:
            fail("observer port 28120 is occupied; stop the previous observer normally")
    validate_known_fault(LABEL, TARGET, INTENSITY)
    isolated.inspect_healthy()
    validate_primary_stack()
    for path in (isolated.FIXTURE / "fault-control/active_fault.json",
                 ROOT / "data/fault-control/active_fault.json"):
        if path.exists() or path.is_symlink():
            fail("an isolated or original fault lease is active")
    names = watcher.check_contract()
    # The gateway retains at most 180 seconds. Do not clear or mix its buffer.
    for attempt in range(25 if wait_stale else 1):
        try:
            state = no_recent_data()
            break
        except (RuntimeError, ValueError) as error:
            if not wait_stale or "existing telemetry watermark is only" not in str(error) or attempt == 24:
                raise
            print("WAIT: prior research telemetry is aging out; no reset or traffic", flush=True)
            import time
            time.sleep(10)
    print("PASS: isolated and original stacks distinct and healthy; fault leases idle")
    print("PASS: frozen Candidate 04 and 65-feature schema; isolated buffer", state)
    print("READ ONLY: no fault, transactions, telemetry POST, inference or model writes")
    return names


def evaluate(rows: list[dict], receipts: list[dict], *, activated: datetime,
             cleared: datetime) -> dict:
    """Judge independently observed effect and frozen output; never use labels as features."""
    if len(rows) != COUNT or len({r.get("request_id") for r in rows}) != COUNT:
        fail("client observation count or identity incomplete")
    failed = sum(r.get("transport_error") is not None or
                 (type(r.get("status_code")) is int and r["status_code"] >= 500) or
                 r.get("expected_outcome") is False for r in rows)
    valid = []
    for item in receipts:
        start = datetime.fromisoformat(item["window_started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(item["window_ended_at"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or end-start != timedelta(seconds=60):
            fail("prediction window lacks an exact timezone-aware 60 seconds")
        if (start >= activated - timedelta(seconds=2) and end <= cleared
                and item["request_count"] >= 70 and item["event_count"] >= 70):
            valid.append(item)
    observed_effect = failed >= 10
    predicted = any(row["prediction"] == LABEL for row in valid)
    return {"status": "PASS" if observed_effect and predicted else "MISMATCH",
            "expected_family": LABEL, "fault_target": TARGET,
            "observed_failed_client_requests": failed,
            "observed_effect": observed_effect,
            "eligible_frozen_receipts": valid,
            "family_predicted": predicted,
            "ground_truth_used_for_inference": False,
            "training_eligible": False, "threshold_tuning_eligible": False}


async def run() -> dict:
    from experiments.runner import PlannedRequest, execute_plan
    from experiments.faults.lifecycle import FaultStateStore
    from scripts import faultweave_v2_isolated_db as isolated
    from scripts.faultweave_v2_isolated_telemetry_bridge import write_new
    from scripts import faultweave_v2_candidate04_shadow_watch as watcher
    from scripts.faultweave_v2_final_two_capture import raw_events_for
    from services.gateway.app.intelligence_live import eligible_runtime_event
    from scripts.faultweave_v2_isolated_unknown_capture import validate_primary_stack

    names = preflight(wait_stale=True)
    run_id = "v2observer-" + uuid4().hex[:16]
    store = observer.ObservationStore(run_id)
    before = {p.name for p in watcher.OUT.glob("*.prediction.json")}
    server = ThreadingHTTPServer((observer.HOST, observer.PORT), observer.make_handler(store))
    lock = observer.OUT / "active.lock"
    try:
        write_new(lock, {"run_id": run_id, "started_at": datetime.now(UTC).isoformat(),
                         "research_only": True, "training_eligible": False})
    except BaseException:
        server.server_close()
        raise
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    stop_collecting = asyncio.Event()

    async def collect_loop() -> None:
        while not stop_collecting.is_set():
            await asyncio.sleep(5)
            print("OBSERVATION:", await asyncio.to_thread(observer.collect_once, store, names), flush=True)

    task = asyncio.create_task(collect_loop())
    fault = FaultStateStore(isolated.FIXTURE / "fault-control")
    activation = None
    rows: list[dict] = []
    cleared = datetime.now(UTC)
    started = datetime.now(UTC)
    try:
        activation = fault.acquire(run_id=run_id, fault_id=LABEL, target=TARGET,
                                   intensity=INTENSITY, duration_seconds=LEASE_SECONDS)
        activation_time = activation.activated_at
        print("ISOLATED FAULT RUN:", run_id, LABEL, TARGET, "113 transactions over ~75s", flush=True)
        plan = [PlannedRequest(sequence=i+1, offset_seconds=i/RATE,
                               scenario="valid", amount_minor=4000+i%100)
                for i in range(COUNT)]
        try:
            rows = await asyncio.wait_for(execute_plan(plan,
                f"http://{observer.HOST}:{observer.PORT}", run_id, 3304, 16), timeout=100)
        finally:
            fault.release(activation.activation_id)
            cleared = datetime.now(UTC)
            activation = None
        # A separate run ID keeps recovery traffic out of the fault window.
        recovery_run_id = "v2recovery-" + uuid4().hex[:16]
        recovery_plan = [PlannedRequest(sequence=10001+i, offset_seconds=i/RATE,
                                        scenario="valid", amount_minor=5000+i)
                         for i in range(RECOVERY_COUNT)]
        recovery = await execute_plan(recovery_plan, "http://127.0.0.1:28110",
                                      recovery_run_id, 3305, 8)
        if (len(recovery) != RECOVERY_COUNT or any(r.get("status_code") != 200 or
                r.get("expected_outcome") is not True or r.get("transport_error")
                for r in recovery)):
            fail("isolated recovery transactions did not all complete")
        for _ in range(12):
            if task.done():
                await task  # surface the collector failure; no silent retry
            await asyncio.sleep(3)
            observed, _, posted = store.snapshot()
            receipts = pilot.new_receipts(before, started)
            if len(observed) == COUNT and len(posted) == COUNT and receipts:
                break
        else:
            fail("complete client telemetry and a new frozen receipt were not observed")
        if fault.active() is not None:
            fail("isolated fault lease did not clear")
        isolated.inspect_healthy()
        validate_primary_stack()
        raw = raw_events_for(run_id, True)
        observed, _, _ = store.snapshot()
        related = [event for event in raw if event.get("run_id") == run_id and
                   event.get("request_id") in observed and
                   event.get("trace_id") == observed[event["request_id"]]["trace_id"] and
                   eligible_runtime_event(event)]
        if not related:
            fail("no independently correlated service events for isolated client requests")
        failure_events = [event for event in related if event.get("outcome") == "failure" or
                          (type(event.get("status_code")) is int and event["status_code"] >= 500)]
        evidence = [{"timestamp": event.get("timestamp"), "service": event.get("service"),
                     "downstream_service": event.get("downstream_service"),
                     "event_type": event.get("event_type"),
                     "event_sha256": hashlib.sha256(json.dumps(event, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode()).hexdigest()}
                    for event in failure_events[:12]]
        # Validate the new receipt's exact window, model hash and source fingerprint
        # through the existing watcher and the independently observed report.
        permitted = {item["receipt_sha256"] for item in receipts}
        all_receipts = []
        for p in watcher.OUT.glob("*.prediction.json"):
            if p.name in before:
                continue
            raw = p.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest not in permitted:
                continue
            data = json.loads(raw)
            all_receipts.append({key: data[key] for key in
                                 ("window_started_at", "window_ended_at", "request_count",
                                  "event_count", "prediction")}
                             | {"receipt_sha256": digest})
        result = evaluate(rows, all_receipts, activated=activation_time, cleared=cleared)
        result["service_failure_evidence_observed"] = bool(failure_events)
        if not failure_events:
            result["status"] = "MISMATCH"
        result.update(run_id=run_id, created_at=datetime.now(UTC).isoformat(),
                      frozen_model_sha256=watcher.MODEL_SHA256,
                      total_client_requests=COUNT, original_gateway_written=False,
                      correlated_service_event_count=len(related),
                      failed_service_event_count=len(failure_events),
                      first_failure_evidence=evidence,
                      recovery_run_id=recovery_run_id,
                      recovery_completed_requests=RECOVERY_COUNT,
                      fault_cleared=True)
        report = OUT / (run_id + ".result.json")
        write_new(report, result)
        print("ACCEPTANCE:", result["status"], "failed requests", result["observed_failed_client_requests"],
              "matched prediction", result["family_predicted"], flush=True)
        print("CREATE-ONLY REPORT:", report, flush=True)
        return result
    finally:
        cleanup_error = None
        if activation is not None:
            try:
                fault.release(activation.activation_id)
            except Exception as error:
                cleanup_error = error
        stop_collecting.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        try:
            await asyncio.to_thread(observer.collect_once, store, names)
            observed, _, posted = store.snapshot()
            if set(observed) <= posted:
                lock.unlink()
                print("PASS: observer stopped with all client observations ingested; isolated fault released")
            else:
                print("STOP: unposted observations remain; observer lock preserved for review")
        except Exception as error:
            print("STOP: observer final collection failed; lock preserved:", error)
        if cleanup_error is not None:
            raise RuntimeError("isolated fault lease cleanup failed; inspect the lease") from cleanup_error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--acknowledge-isolated-payment-fault-and-121-transactions", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight:
        preflight()
    else:
        if not args.acknowledge_isolated_payment_fault_and_121_transactions:
            fail("explicit isolated fault and transaction acknowledgement required")
        result = asyncio.run(run())
        return 0 if result["status"] == "PASS" else 3
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("ISOLATED FAULT ACCEPTANCE STOP:", error)
        raise SystemExit(2)
