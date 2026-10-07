from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from experiments.faults.adapters import (  # noqa: E402
    activate_infrastructure_fault,
    deactivate_infrastructure_fault,
    verify_stack_recovery,
)
from experiments.faults.catalog import (  # noqa: E402
    FaultFamily,
    FaultIntensity,
    get_known_fault,
)
from experiments.faults.lifecycle import FaultLease, FaultStateStore  # noqa: E402

CONTROL_DIRECTORY = PROJECT_DIRECTORY / "data" / "fault-control"


def run_fault(fault_id: str, target: str, intensity: str, duration: float) -> None:
    definition = get_known_fault(fault_id)
    if definition.mechanism == "traffic" or fault_id == FaultFamily.CONNECTION_POOL_EXHAUSTION:
        raise ValueError("traffic and pool faults require the traffic probe")
    store = FaultStateStore(CONTROL_DIRECTORY)
    activation = store.acquire(
        run_id=f"live-demo-{uuid4().hex[:12]}",
        fault_id=fault_id,
        target=target,
        intensity=intensity,
        duration_seconds=duration,
    )
    print(
        f"FAULT_ACTIVE fault={fault_id} target={target} "
        f"intensity={intensity} duration={duration:.0f}s",
        flush=True,
    )
    with FaultLease(
        store,
        activation,
        activate_infrastructure_fault,
        deactivate_infrastructure_fault,
        verify_stack_recovery,
    ):
        time.sleep(duration)
    print("RECOVERY_VERIFIED normal transaction smoke flow passed", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hold one bounded known fault while the live traffic process supplies evidence"
    )
    parser.add_argument("--fault", choices=[item.value for item in FaultFamily], required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--intensity", choices=[item.value for item in FaultIntensity], required=True
    )
    parser.add_argument("--duration", type=float, default=20.0)
    args = parser.parse_args()
    if not 10 <= args.duration <= 30:
        parser.error("--duration must be between 10 and 30 seconds")
    return args


def main() -> int:
    args = parse_args()
    try:
        run_fault(args.fault, args.target, args.intensity, args.duration)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
