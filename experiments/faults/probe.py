from __future__ import annotations

import argparse
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from experiments.faults.adapters import (
    activate_infrastructure_fault,
    deactivate_infrastructure_fault,
    verify_stack_recovery,
)
from experiments.faults.catalog import FaultFamily, FaultIntensity, get_known_fault
from experiments.faults.lifecycle import FaultLease, FaultStateStore
from experiments.manifest import write_json

PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
CONTROL_DIRECTORY = PROJECT_DIRECTORY / "data" / "fault-control"
OUTPUT_DIRECTORY = PROJECT_DIRECTORY / "data" / "experiments" / "fault-probes"


def request_once(gateway_url: str, run_id: str, sequence: int) -> dict:
    started = time.perf_counter()
    status_code: int | None = None
    error_type: str | None = None
    try:
        response = httpx.post(
            f"{gateway_url}/api/v1/transactions",
            headers={
                "X-Run-ID": run_id,
                "X-Request-ID": f"{run_id}-request-{sequence:04d}",
                "X-Trace-ID": str(uuid4()),
            },
            json={
                "username": "demo", "password": "faultweave-demo",
                "account_number": "FW-DEMO-001", "amount_minor": 1000,
                "currency": "INR", "recipient": f"fault-probe-{sequence:04d}",
            },
            timeout=15,
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


def run_probe(
    fault_id: str, target: str, intensity: str, duration_seconds: float,
    gateway_url: str,
) -> Path:
    definition = get_known_fault(fault_id)
    if definition.mechanism == "traffic":
        raise ValueError("traffic faults use the Phase 4C traffic injector")
    run_id = f"controlled-{uuid4().hex[:12]}"
    store = FaultStateStore(CONTROL_DIRECTORY)
    activation = store.acquire(
        run_id=run_id, fault_id=fault_id, target=target,
        intensity=intensity, duration_seconds=duration_seconds,
    )
    results: list[dict] = []
    with FaultLease(
        store, activation, activate_infrastructure_fault,
        deactivate_infrastructure_fault, verify_stack_recovery,
    ):
        deadline = time.monotonic() + duration_seconds
        sequence = 1
        while time.monotonic() < deadline:
            results.append(request_once(gateway_url, run_id, sequence))
            sequence += 1

    abnormal = any(
        result["transport_error"] is not None
        or result["status_code"] is None
        or result["status_code"] >= 500
        or result["latency_ms"] >= 200
        for result in results
    )
    if not abnormal:
        raise RuntimeError("controlled fault produced no observable abnormal behaviour")
    output = OUTPUT_DIRECTORY / f"{run_id}.json"
    write_json(
        output,
        {
            "schema_version": "1.0", "run_id": run_id, "run_type": "known_fault_probe",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "fault": activation.model_dump(mode="json"), "results": results,
            "recovery_verified": True,
        },
    )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded known-fault probe")
    parser.add_argument("--fault", choices=[item.value for item in FaultFamily], required=True)
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
    if not 1 <= args.duration <= 30:
        parser.error("--duration must be between 1 and 30 seconds for a probe")
    return args


def main() -> int:
    args = parse_args()
    try:
        path = run_probe(args.fault, args.target, args.intensity, args.duration, args.gateway_url)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print("PASS: Controlled known-fault probe completed and recovery verified")
    print(f"Artifact: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
