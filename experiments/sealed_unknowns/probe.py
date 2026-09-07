from __future__ import annotations

import argparse
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from experiments.faults.adapters import verify_stack_recovery
from experiments.faults.catalog import FaultIntensity
from experiments.faults.probe import request_once
from experiments.manifest import write_json

from .catalog import SealedUnknownFamily
from .lifecycle import SealedUnknownLease, SealedUnknownStateStore

PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
CONTROL_DIRECTORY = PROJECT_DIRECTORY / "data" / "fault-control"
OUTPUT_DIRECTORY = (
    PROJECT_DIRECTORY / "data" / "experiments" / "sealed-unknown-evaluation"
)


def run_probe(
    fault_id: str,
    target: str,
    intensity: str,
    duration_seconds: float,
    gateway_url: str,
) -> Path:
    run_id = f"sealed-unknown-{uuid4().hex[:12]}"
    store = SealedUnknownStateStore(CONTROL_DIRECTORY)
    activation = store.acquire(
        run_id=run_id,
        fault_id=fault_id,
        target=target,
        intensity=intensity,
        duration_seconds=duration_seconds,
    )
    results: list[dict] = []
    with SealedUnknownLease(store, activation, verify_stack_recovery):
        deadline = time.monotonic() + duration_seconds
        sequence = 1
        while time.monotonic() < deadline:
            results.append(request_once(gateway_url, run_id, sequence))
            sequence += 1

    abnormal = any(
        item["transport_error"] is not None
        or item["status_code"] is None
        or item["status_code"] >= 500
        or item["latency_ms"] >= 200
        for item in results
    )
    if not results or not abnormal:
        raise RuntimeError("sealed scenario produced no observable abnormal behaviour")

    output = OUTPUT_DIRECTORY / f"{run_id}.json"
    write_json(
        output,
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "run_type": "sealed_unknown_evaluation",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "ground_truth": activation.model_dump(mode="json"),
            "results": results,
            "recovery_verified": True,
            "training_eligible": False,
            "threshold_tuning_eligible": False,
        },
    )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one sealed unknown evaluation probe")
    parser.add_argument(
        "--scenario", choices=[item.value for item in SealedUnknownFamily], required=True
    )
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--intensity", choices=[item.value for item in FaultIntensity], required=True
    )
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110"),
    )
    args = parser.parse_args()
    if not 3 <= args.duration <= 30:
        parser.error("--duration must be between 3 and 30 seconds for a probe")
    return args


def main() -> int:
    args = parse_args()
    try:
        path = run_probe(
            args.scenario, args.target, args.intensity, args.duration, args.gateway_url
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print("PASS: Sealed unknown probe completed and recovery verified")
    print(f"Artifact: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
