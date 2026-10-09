"""Run a sustained transfer fault and require evaluated frozen-model intelligence."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.faults.adapters import (  # noqa: E402
    activate_infrastructure_fault,
    deactivate_infrastructure_fault,
    verify_stack_recovery,
)
from experiments.faults.lifecycle import FaultLease, FaultStateStore  # noqa: E402
from experiments.manifest import write_json  # noqa: E402

CONTROL_DIRECTORY = ROOT / "data" / "fault-control"
OUTPUT_DIRECTORY = ROOT / "data" / "experiments" / "transfer-live-intelligence"
BASELINE_SECONDS = 15.0
FAULT_SECONDS = 25.0
RECOVERY_SECONDS = 20.0
TARGET_RPS = 3.0
MINIMUM_FAULT_AFFECTED_RATE = 0.80
MINIMUM_DURABLE_REVIEW_RATE = 0.10
RECONCILIATION_ATTEMPTS = 8
RECONCILIATION_DELAY_SECONDS = 0.25


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def request_observation(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": result["request_id"],
        "trace_id": result["trace_id"],
        "started_at": result["started_at"],
        "latency_ms": result["latency_ms"],
        "status_code": result["status_code"],
        "expected_outcome": result["expected_outcome"],
        "transport_error": result["transport_error"],
        "scenario": "valid",
    }


def validate_phase(
    name: str,
    rows: list[dict[str, Any]],
    expected_state: str,
    minimum_rate: float = 1.0,
) -> None:
    if not rows:
        raise RuntimeError(f"{name} produced no transfer observations")
    matching = sum(row.get("state") == expected_state for row in rows)
    rate = matching / len(rows)
    if rate < minimum_rate:
        raise RuntimeError(
            f"{name} expected {expected_state} rate >= {minimum_rate:.0%}, observed {rate:.1%}"
        )


def validate_fault_phase(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    """Require broad customer impact plus durable engineer-review evidence."""
    if not rows:
        raise RuntimeError("fault produced no transfer observations")
    reviewed = sum(row.get("state") == "REQUIRES_REVIEW" for row in rows)
    server_errors = sum(
        row.get("status_code") is not None and int(row["status_code"]) >= 500
        for row in rows
    )
    transport_errors = sum(row.get("transport_error") is not None for row in rows)
    affected = sum(
        row.get("state") == "REQUIRES_REVIEW"
        or (row.get("status_code") is not None and int(row["status_code"]) >= 500)
        or row.get("transport_error") is not None
        for row in rows
    )
    affected_rate = affected / len(rows)
    review_rate = reviewed / len(rows)
    if affected_rate < MINIMUM_FAULT_AFFECTED_RATE:
        raise RuntimeError(
            "fault expected customer-impact rate "
            f">= {MINIMUM_FAULT_AFFECTED_RATE:.0%}, observed {affected_rate:.1%}"
        )
    if review_rate < MINIMUM_DURABLE_REVIEW_RATE:
        raise RuntimeError(
            "fault expected durable REQUIRES_REVIEW rate "
            f">= {MINIMUM_DURABLE_REVIEW_RATE:.0%}, observed {review_rate:.1%}"
        )
    return {
        "affected": affected,
        "affected_rate": round(affected_rate, 6),
        "requires_review": reviewed,
        "requires_review_rate": round(review_rate, 6),
        "server_errors": server_errors,
        "transport_errors": transport_errors,
    }


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


async def reconcile_transfer(
    client: httpx.AsyncClient,
    *,
    idempotency_key: str,
    payload: dict[str, Any],
    attempts: int = RECONCILIATION_ATTEMPTS,
    delay_seconds: float = RECONCILIATION_DELAY_SECONDS,
) -> dict[str, Any]:
    """Resolve an uncertain HTTP outcome from the durable workflow identity.

    The initial client response remains unchanged in request telemetry. This
    lookup only establishes whether PostgreSQL contains the transfer and, when
    the request failed before durable receipt, safely replays the same payload
    with the same idempotency key.
    """
    last_status: int | None = None
    for attempt in range(1, attempts + 1):
        try:
            lookup = await client.get(f"/api/v1/transfers/by-key/{idempotency_key}")
            last_status = lookup.status_code
            if lookup.status_code == 200:
                return lookup.json()
            if lookup.status_code == 404:
                replay = await client.post("/api/v1/transfers", json=payload)
                last_status = replay.status_code
                if replay.status_code == 200:
                    return replay.json()
        except httpx.RequestError:
            pass
        if attempt < attempts:
            await asyncio.sleep(delay_seconds * attempt)
    raise RuntimeError(
        "unable to reconcile transfer "
        f"{idempotency_key} after {attempts} attempts; last HTTP status={last_status}"
    )


async def reconcile_pending_rows(
    client: httpx.AsyncClient, rows: list[dict[str, Any]]
) -> None:
    """Resolve uncertain requests after the relevant dependency is restored."""
    for row in rows:
        pending = row.pop("pending_reconciliation", None)
        if pending is None:
            continue
        body = await reconcile_transfer(
            client,
            idempotency_key=pending["idempotency_key"],
            payload=pending["payload"],
        )
        row["state"] = body.get("state")
        row["transfer_id"] = body.get("id")


async def run_phase(
    client: httpx.AsyncClient,
    *,
    run_id: str,
    phase: str,
    source: str,
    destination: str,
    duration_seconds: float,
    rps: float,
    sequence_start: int,
    defer_reconciliation: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    count = max(1, round(duration_seconds * rps))
    started_clock = time.perf_counter()

    async def execute(sequence: int) -> dict[str, Any]:
        offset = (sequence - sequence_start) / rps
        delay = offset - (time.perf_counter() - started_clock)
        if delay > 0:
            await asyncio.sleep(delay)
        request_id = f"{run_id}-{phase}-{sequence:04d}"
        trace_id = str(uuid4())
        started_at = datetime.now(UTC)
        clock = time.perf_counter()
        status_code = None
        transport_error = None
        body: dict[str, Any] = {}
        idempotency_key = f"{run_id}-{phase}-{sequence:04d}"
        payload = {
            "source_account_number": source,
            "destination_account_number": destination,
            "amount_minor": 1,
            "currency": "INR",
            "idempotency_key": idempotency_key,
        }
        try:
            response = await client.post(
                "/api/v1/transfers",
                headers={
                    "X-Run-ID": run_id,
                    "X-Request-ID": request_id,
                    "X-Trace-ID": trace_id,
                },
                json=payload,
            )
            status_code = response.status_code
            if response.status_code == 200:
                body = response.json()
        except httpx.RequestError as exc:
            transport_error = type(exc).__name__
        latency_ms = round((time.perf_counter() - clock) * 1000, 3)
        state = body.get("state")
        result = {
            "phase": phase,
            "sequence": sequence,
            "request_id": request_id,
            "trace_id": trace_id,
            "started_at": _iso(started_at),
            "latency_ms": latency_ms,
            "status_code": status_code,
            "transport_error": transport_error,
            "expected_outcome": status_code == 200 and state == "COMPLETED",
            "state": state,
            "transfer_id": body.get("id"),
        }
        uncertain = state is None
        if uncertain:
            if defer_reconciliation:
                result["pending_reconciliation"] = {
                    "idempotency_key": idempotency_key,
                    "payload": payload,
                }
        telemetry = await client.post(
            "/api/v1/intelligence/telemetry",
            json={"requests": [request_observation(result)]},
        )
        telemetry.raise_for_status()
        if uncertain and not defer_reconciliation:
            await reconcile_pending_rows(client, [result])
        return result

    tasks = [asyncio.create_task(execute(sequence_start + index)) for index in range(count)]
    for result in await asyncio.gather(*tasks):
        rows.append(result)
    return sorted(rows, key=lambda item: item["sequence"])


async def resume_cases(
    engineer: httpx.AsyncClient, run_id: str, rows: list[dict[str, Any]]
) -> None:
    review_rows = [row for row in rows if row.get("state") == "REQUIRES_REVIEW"]
    for index, row in enumerate(review_rows, start=1):
        response = await engineer.post(
            f"/api/v1/transfers/{row['transfer_id']}/resume",
            headers={
                "X-Run-ID": run_id,
                "X-Request-ID": f"{run_id}-review-{index:04d}",
                "X-Trace-ID": str(uuid4()),
            },
            json={"note": "Engineer reviewed the payment outage evidence and restored the service"},
        )
        response.raise_for_status()
        if response.json().get("state") != "COMPLETED":
            raise RuntimeError(f"recovery did not complete transfer {row['transfer_id']}")


def start_bridge(gateway_url: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "scripts/live_telemetry_bridge.py",
            "--gateway-url",
            gateway_url,
            "--since",
            "3s",
            "--flush-interval",
            "0.25",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop_bridge(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def acceptance(args: argparse.Namespace) -> dict[str, Any]:
    run_id = f"phase16d-{uuid4().hex[:12]}"
    destination = f"FW-LIVE-{run_id.rsplit('-', 1)[-1].upper()}"
    timeout = httpx.Timeout(75.0)
    async with (
        httpx.AsyncClient(base_url=args.gateway_url, timeout=timeout) as customer,
        httpx.AsyncClient(base_url=args.gateway_url, timeout=timeout) as engineer,
    ):
        customer_token = await login(customer, args.customer_username, args.customer_password)
        engineer_token = await login(engineer, args.engineer_username, args.engineer_password)
        customer.headers["Authorization"] = f"Bearer {customer_token}"
        engineer.headers["Authorization"] = f"Bearer {engineer_token}"
        source_response = await customer.get(f"/api/v1/accounts/{args.source_account}")
        source_response.raise_for_status()
        destination_response = await customer.post(
            "/api/v1/accounts", json={"account_number": destination}
        )
        destination_response.raise_for_status()

        baseline = await run_phase(
            customer,
            run_id=run_id,
            phase="baseline",
            source=args.source_account,
            destination=destination,
            duration_seconds=args.baseline_seconds,
            rps=args.rps,
            sequence_start=1,
        )
        validate_phase("baseline", baseline, "COMPLETED")

        store = FaultStateStore(CONTROL_DIRECTORY)
        activation = store.acquire(
            run_id=run_id,
            fault_id="SERVICE_UNAVAILABLE",
            target="payment",
            intensity="medium",
            duration_seconds=args.fault_seconds + 20,
        )
        with FaultLease(
            store,
            activation,
            activate_infrastructure_fault,
            deactivate_infrastructure_fault,
            verify_stack_recovery,
        ):
            fault = await run_phase(
                customer,
                run_id=run_id,
                phase="fault",
                source=args.source_account,
                destination=destination,
                duration_seconds=args.fault_seconds,
                rps=args.rps,
                sequence_start=len(baseline) + 1,
            defer_reconciliation=True,
            )
        # The lease has restored payment by this point. Reconcile 5xx and
        # transport-uncertain attempts now, against the original durable key.
        await reconcile_pending_rows(customer, fault)
        fault_outcomes = validate_fault_phase(fault)
        await resume_cases(engineer, run_id, fault)

        recovery = await run_phase(
            customer,
            run_id=run_id,
            phase="recovery",
            source=args.source_account,
            destination=destination,
            duration_seconds=args.recovery_seconds,
            rps=args.rps,
            sequence_start=len(baseline) + len(fault) + 1,
        )
        validate_phase("recovery", recovery, "COMPLETED")
        await asyncio.sleep(2.0)

        # The last reviewed case has the freshest recovery event and therefore
        # overlaps the final rolling window even when many cases pause.
        selected = next(
            row
            for row in reversed(fault)
            if row.get("state") == "REQUIRES_REVIEW"
        )
        report_response = await customer.get(
            f"/api/v1/transfers/{selected['transfer_id']}/intelligence"
        )
        report_response.raise_for_status()
        report = report_response.json()
        model = report.get("model") or {}
        if model.get("status") != "EVALUATED":
            raise RuntimeError(f"model was not evaluated: {model.get('status')}")
        result = model.get("result") or {}
        if result.get("status") != "ABNORMAL":
            raise RuntimeError(f"fault window was not detected as abnormal: {result}")
        if report.get("operational_facts", {}).get("recovered_after_review") is not True:
            raise RuntimeError("selected transfer lacks reviewed recovery evidence")
        return {
            "schema_version": "1.0",
            "run_id": run_id,
            "created_at": _iso(datetime.now(UTC)),
            "profile": {
                "rps": args.rps,
                "baseline_seconds": args.baseline_seconds,
                "fault_seconds": args.fault_seconds,
                "recovery_seconds": args.recovery_seconds,
            },
            "request_counts": {
                "baseline": len(baseline),
                "fault": len(fault),
                "recovery": len(recovery),
            },
            "paused_transfers": sum(row.get("state") == "REQUIRES_REVIEW" for row in fault),
            "fault_outcomes": fault_outcomes,
            "selected_transfer_id": selected["transfer_id"],
            "model": model,
            "observed_origin": report.get("probable_origin"),
            "recovery_verified": True,
            "ground_truth_used_by_inference": False,
        }


def parse_args() -> argparse.Namespace:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", default="http://localhost:18110")
    parser.add_argument("--rps", type=float, default=TARGET_RPS)
    parser.add_argument("--baseline-seconds", type=float, default=BASELINE_SECONDS)
    parser.add_argument("--fault-seconds", type=float, default=FAULT_SECONDS)
    parser.add_argument("--recovery-seconds", type=float, default=RECOVERY_SECONDS)
    parser.add_argument("--customer-username", default=os.getenv("DEMO_USERNAME", "demo"))
    parser.add_argument(
        "--customer-password", default=os.getenv("DEMO_PASSWORD", "faultweave-demo")
    )
    parser.add_argument("--engineer-username", default=os.getenv("ENGINEER_USERNAME", "engineer"))
    parser.add_argument(
        "--engineer-password",
        default=os.getenv("ENGINEER_PASSWORD", "faultweave-engineer"),
    )
    parser.add_argument("--source-account", default=os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001"))
    args = parser.parse_args()
    if min(args.rps, args.baseline_seconds, args.fault_seconds, args.recovery_seconds) <= 0:
        parser.error("rates and phase durations must be positive")
    if args.baseline_seconds + args.fault_seconds + args.recovery_seconds < 60:
        parser.error("workload phases must cover at least 60 seconds")
    return args


def main() -> int:
    args = parse_args()
    bridge = start_bridge(args.gateway_url)
    try:
        time.sleep(2.0)
        if bridge.poll() is not None:
            detail = bridge.stderr.read().strip() if bridge.stderr else ""
            raise RuntimeError(f"telemetry bridge stopped early: {detail}")
        report = asyncio.run(acceptance(args))
        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        output = OUTPUT_DIRECTORY / f"{report['run_id']}.json"
        write_json(output, report)
    finally:
        stop_bridge(bridge)
    classification = report["model"]["result"]["classification"]
    print("PASS: Phase 16D sustained live-transfer intelligence verified")
    print(f"Run ID: {report['run_id']}")
    print(f"Requests: {report['request_counts']}")
    print(f"Paused and recovered transfers: {report['paused_transfers']}")
    print(f"Fault outcomes: {report['fault_outcomes']}")
    print(f"Model status: {report['model']['status']}")
    print(f"Anomaly decision: {report['model']['result']['status']}")
    print(f"Classification: {classification['label']}")
    print(f"Observed origin: {report['observed_origin']['service']}")
    print(f"Evidence: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: Phase 16D verification failed: {exc}")
        raise SystemExit(1) from exc
