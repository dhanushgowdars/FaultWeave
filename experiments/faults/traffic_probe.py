from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from experiments.faults.adapters import verify_stack_recovery
from experiments.faults.catalog import FaultFamily, FaultIntensity, validate_known_fault
from experiments.faults.lifecycle import FaultStateStore
from experiments.manifest import write_json

PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
OUTPUT_DIRECTORY = PROJECT_DIRECTORY / "data" / "experiments" / "fault-probes"
CONTROL_DIRECTORY = PROJECT_DIRECTORY / "data" / "fault-control"
CALIBRATION_PATH = (
    PROJECT_DIRECTORY / "data" / "experiments" / "calibration" / "healthy_envelope.json"
)
_AUTH_RPS = {"mild": 20.0, "medium": 40.0, "high": 60.0}
_LOAD_MULTIPLIER = {"mild": 1.5, "medium": 2.0, "high": 2.5}
_POOL_CONNECTIONS = {"mild": 8, "medium": 12, "high": 15}


def calibrated_healthy_rps() -> float:
    try:
        payload = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
        selected = float(payload["selected_high_healthy_rps"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("valid Phase 3 healthy-envelope calibration is required") from exc
    if selected <= 0:
        raise RuntimeError("healthy-envelope calibration has no selected rate")
    return selected


def fault_rate(fault_id: str, intensity: str) -> float:
    if fault_id == FaultFamily.AUTHENTICATION_FAILURE_BURST:
        return _AUTH_RPS[intensity]
    if fault_id == FaultFamily.HIGH_LOAD:
        return calibrated_healthy_rps() * _LOAD_MULTIPLIER[intensity]
    if fault_id == FaultFamily.CONNECTION_POOL_EXHAUSTION:
        return 3.0
    raise ValueError("traffic probe supports only the three remaining core faults")


def request_payload(fault_id: str, sequence: int) -> dict:
    password = "invalid-burst-password"
    if fault_id != FaultFamily.AUTHENTICATION_FAILURE_BURST:
        password = "faultweave-demo"
    return {
        "username": "demo",
        "password": password,
        "account_number": "FW-DEMO-001",
        "amount_minor": 1000 + sequence,
        "currency": "INR",
        "recipient": f"controlled-probe-{sequence:05d}",
    }


async def execute_request(
    client: httpx.AsyncClient, gateway_url: str, run_id: str, fault_id: str, sequence: int
) -> dict:
    started = time.perf_counter()
    status_code: int | None = None
    error_type: str | None = None
    try:
        response = await client.post(
            f"{gateway_url}/api/v1/transactions",
            headers={
                "X-Run-ID": run_id,
                "X-Request-ID": f"{run_id}-request-{sequence:05d}",
                "X-Trace-ID": str(uuid4()),
            },
            json=request_payload(fault_id, sequence),
        )
        status_code = response.status_code
    except httpx.RequestError as exc:
        error_type = type(exc).__name__
    return {
        "sequence": sequence,
        "status_code": status_code,
        "transport_error": error_type,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


async def execute_traffic(
    gateway_url: str, run_id: str, fault_id: str, target_rps: float, duration: float
) -> tuple[list[dict], float]:
    tasks = []
    count = max(1, round(target_rps * duration))
    started = time.perf_counter()
    limits = httpx.Limits(max_connections=150, max_keepalive_connections=75)
    async with httpx.AsyncClient(timeout=15, limits=limits) as client:
        for sequence in range(1, count + 1):
            delay = (sequence - 1) / target_rps - (time.perf_counter() - started)
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(
                asyncio.create_task(
                    execute_request(client, gateway_url, run_id, fault_id, sequence)
                )
            )
        results = await asyncio.gather(*tasks)
    return results, time.perf_counter() - started


def percentile_95(results: list[dict]) -> float:
    ordered = sorted(float(result["latency_ms"]) for result in results)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def run_traffic_probe(
    fault_id: str,
    target: str,
    intensity: str,
    duration: float,
    gateway_url: str,
) -> Path:
    validate_known_fault(fault_id, target, intensity)
    if fault_id not in {
        FaultFamily.AUTHENTICATION_FAILURE_BURST,
        FaultFamily.HIGH_LOAD,
        FaultFamily.CONNECTION_POOL_EXHAUSTION,
    }:
        raise ValueError("use experiments.faults.probe for infrastructure faults")
    run_id = f"controlled-{uuid4().hex[:12]}"
    store = FaultStateStore(CONTROL_DIRECTORY)
    activation = store.acquire(
        run_id=run_id,
        fault_id=fault_id,
        target=target,
        intensity=intensity,
        duration_seconds=duration,
    )
    target_rps = fault_rate(fault_id, intensity)
    configured_pool_pressure = 0
    results: list[dict] = []
    actual_duration = 0.0
    try:
        if fault_id == FaultFamily.CONNECTION_POOL_EXHAUSTION:
            configured_pool_pressure = _POOL_CONNECTIONS[intensity]
        results, actual_duration = asyncio.run(
            execute_traffic(gateway_url, run_id, fault_id, target_rps, duration)
        )
    finally:
        store.release(activation.activation_id)
        verify_stack_recovery()

    statuses = Counter(result["status_code"] for result in results)
    achieved_rps = len(results) / actual_duration
    if fault_id == FaultFamily.AUTHENTICATION_FAILURE_BURST and statuses[401] < len(results) * 0.9:
        raise RuntimeError(
            "authentication burst did not produce the expected rejected-login volume"
        )
    if fault_id == FaultFamily.HIGH_LOAD and target_rps <= calibrated_healthy_rps():
        raise RuntimeError("high-load probe did not exceed the calibrated healthy boundary")
    if fault_id == FaultFamily.HIGH_LOAD:
        abnormal = (
            achieved_rps < target_rps * 0.9
            or percentile_95(results) > 1000
            or any(
                result["transport_error"]
                or (result["status_code"] is not None and result["status_code"] >= 500)
                for result in results
            )
        )
        if not abnormal:
            raise RuntimeError("high load produced no observable overload behaviour")
    if fault_id == FaultFamily.CONNECTION_POOL_EXHAUSTION:
        abnormal = any(
            result["transport_error"] or not result["status_code"]
            or result["status_code"] >= 500 or result["latency_ms"] >= 1000
            for result in results
        )
        if not abnormal:
            raise RuntimeError("pool pressure produced no observable abnormal behaviour")

    output = OUTPUT_DIRECTORY / f"{run_id}.json"
    write_json(
        output,
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "run_type": "known_fault_probe",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "fault": activation.model_dump(mode="json"),
            "target_rps": target_rps,
            "achieved_rps": achieved_rps,
            "p95_latency_ms": percentile_95(results),
            "status_counts": {str(key): value for key, value in statuses.items()},
            "configured_pool_connections_held": configured_pool_pressure,
            "results": results,
            "recovery_verified": True,
        },
    )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded traffic or pool probe")
    parser.add_argument(
        "--fault",
        choices=[
            FaultFamily.AUTHENTICATION_FAILURE_BURST.value,
            FaultFamily.HIGH_LOAD.value,
            FaultFamily.CONNECTION_POOL_EXHAUSTION.value,
        ],
        required=True,
    )
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--intensity", choices=[item.value for item in FaultIntensity], required=True
    )
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110"),
    )
    args = parser.parse_args()
    if not 2 <= args.duration <= 30:
        parser.error("--duration must be between 2 and 30 seconds for a probe")
    return args


def main() -> int:
    args = parse_args()
    try:
        path = run_traffic_probe(
            args.fault, args.target, args.intensity, args.duration, args.gateway_url
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print("PASS: Controlled traffic/pool probe completed and recovery verified")
    print(f"Artifact: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
