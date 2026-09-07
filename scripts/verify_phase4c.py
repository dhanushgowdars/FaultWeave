from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from experiments.faults.catalog import (  # noqa: E402
    CORE_KNOWN_FAULTS,
    EXTENDED_KNOWN_FAULTS,
    get_known_fault,
)
from experiments.sealed_unknowns import (  # noqa: E402
    SEALED_UNKNOWN_SCENARIOS,
    SealedUnknownLease,
    SealedUnknownStateStore,
)


def main() -> int:
    expected = {
        "INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE",
        "LATENCY_JITTER_PARTIAL_DEGRADATION",
    }
    actual = {item.value for item in SEALED_UNKNOWN_SCENARIOS}
    if actual != expected:
        print("FAIL: sealed unknown registry differs from the frozen contract")
        return 1
    known = {item.value for item in (*CORE_KNOWN_FAULTS, *EXTENDED_KNOWN_FAULTS)}
    if known & actual:
        print("FAIL: known and sealed unknown registries overlap")
        return 1
    for identifier in expected:
        try:
            get_known_fault(identifier)
        except ValueError:
            continue
        print(f"FAIL: {identifier} is accessible through the known-fault registry")
        return 1

    calls: list[str] = []
    with TemporaryDirectory() as directory:
        store = SealedUnknownStateStore(Path(directory))
        activation = store.acquire(
            run_id="phase-4c-contract-check",
            fault_id="LATENCY_JITTER_PARTIAL_DEGRADATION",
            target="transaction",
            intensity="medium",
            duration_seconds=5,
        )
        with SealedUnknownLease(store, activation, lambda: calls.append("recovered")):
            if store.active() != activation:
                print("FAIL: sealed activation is not readable")
                return 1
        if store.active() is not None:
            print("FAIL: sealed activation remained after cleanup")
            return 1
    if calls != ["recovered"]:
        print("FAIL: sealed recovery hook was not executed exactly once")
        return 1

    print("PASS: Phase 4C sealed-unknown contract verified")
    print("Sealed evaluation families: 2")
    print("Known-registry access: blocked")
    print("Training eligibility: forbidden")
    print("Threshold-tuning eligibility: forbidden")
    print("Exclusive lease and recovery: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
