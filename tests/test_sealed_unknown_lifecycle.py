import json
from datetime import UTC, datetime, timedelta

import pytest

from experiments.faults.lifecycle import FaultStateStore
from experiments.sealed_unknowns.lifecycle import (
    SealedUnknownLease,
    SealedUnknownStateStore,
)


def test_unknown_store_uses_global_exclusive_fault_lease(tmp_path) -> None:
    known_store = FaultStateStore(tmp_path)
    known = known_store.acquire(
        run_id="known-run",
        fault_id="SERVICE_UNAVAILABLE",
        target="payment",
        intensity="medium",
        duration_seconds=5,
    )
    unknown_store = SealedUnknownStateStore(tmp_path)
    with pytest.raises(RuntimeError, match="global lease"):
        unknown_store.acquire(
            run_id="unknown-run",
            fault_id="LATENCY_JITTER_PARTIAL_DEGRADATION",
            target="transaction",
            intensity="medium",
            duration_seconds=5,
        )
    known_store.release(known.activation_id)


def test_known_store_refuses_to_parse_active_unknown(tmp_path) -> None:
    unknown_store = SealedUnknownStateStore(tmp_path)
    activation = unknown_store.acquire(
        run_id="unknown-run",
        fault_id="LATENCY_JITTER_PARTIAL_DEGRADATION",
        target="transaction",
        intensity="medium",
        duration_seconds=5,
    )
    with pytest.raises(RuntimeError, match="sealed-unknown"):
        FaultStateStore(tmp_path).active()
    unknown_store.release(activation.activation_id)


def test_unknown_lease_removes_control_before_recovery(tmp_path) -> None:
    store = SealedUnknownStateStore(tmp_path)
    activation = store.acquire(
        run_id="unknown-run",
        fault_id="LATENCY_JITTER_PARTIAL_DEGRADATION",
        target="transaction",
        intensity="high",
        duration_seconds=5,
    )
    recovery_observations: list[bool] = []
    with SealedUnknownLease(
        store, activation, lambda: recovery_observations.append(store.active() is None)
    ):
        assert store.active() == activation
    assert recovery_observations == [True]


def test_expired_unknown_activation_is_removed(tmp_path) -> None:
    path = tmp_path / "active_fault.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "experiment_scope": "sealed_unknown_evaluation",
                "activation_id": "a" * 32,
                "run_id": "unknown-run",
                "fault_id": "LATENCY_JITTER_PARTIAL_DEGRADATION",
                "target": "transaction",
                "intensity": "medium",
                "duration_seconds": 5,
                "activated_at": datetime.now(UTC).isoformat(),
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert SealedUnknownStateStore(tmp_path).active() is None
    assert not path.exists()
