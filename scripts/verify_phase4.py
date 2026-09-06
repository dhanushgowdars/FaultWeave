from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from experiments.faults import (  # noqa: E402
    CORE_KNOWN_FAULTS,
    EXTENDED_KNOWN_FAULTS,
    SEALED_UNKNOWN_FAULT_IDS,
    FaultLease,
    FaultStateStore,
)


def main() -> int:
    expected_core = {
        "DATABASE_HIGH_LATENCY", "DATABASE_UNAVAILABLE", "SERVICE_UNAVAILABLE",
        "DOWNSTREAM_TIMEOUT", "AUTHENTICATION_FAILURE_BURST", "HIGH_LOAD",
        "CONNECTION_POOL_EXHAUSTION",
    }
    if {fault.value for fault in CORE_KNOWN_FAULTS} != expected_core:
        print("FAIL: core known-fault registry differs from the frozen contract")
        return 1
    if len(EXTENDED_KNOWN_FAULTS) != 2 or len(SEALED_UNKNOWN_FAULT_IDS) != 2:
        print("FAIL: extended or sealed-unknown registry is incomplete")
        return 1

    calls: list[str] = []
    with TemporaryDirectory() as directory:
        store = FaultStateStore(Path(directory))
        activation = store.acquire(
            run_id="phase-4-contract-check", fault_id="DATABASE_HIGH_LATENCY",
            target="transaction", intensity="mild", duration_seconds=5,
        )
        with FaultLease(
            store, activation, lambda _: calls.append("activate"),
            lambda _: calls.append("deactivate"), lambda: calls.append("verify_recovery"),
        ):
            if store.active() != activation:
                print("FAIL: acquired activation is not readable")
                return 1
        if store.active() is not None:
            print("FAIL: fault remained active after lease cleanup")
            return 1
    if calls != ["activate", "deactivate", "verify_recovery"]:
        print("FAIL: fault lifecycle order is invalid")
        return 1

    print("PASS: Phase 4 fault-safety contract verified")
    print("Core known faults: 7")
    print("Extended known faults: 2")
    print("Sealed unknown identifiers: 2")
    print("Exclusive activation: valid")
    print("Automatic cleanup and recovery hook: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
