from datetime import timedelta

import pytest

from experiments.faults.lifecycle import FaultLease, FaultStateStore, utc_now


def acquire(store: FaultStateStore, run_id: str = "fault-run-1"):
    return store.acquire(
        run_id=run_id, fault_id="SERVICE_UNAVAILABLE", target="payment",
        intensity="medium", duration_seconds=30,
    )


def test_store_allows_only_one_active_fault(tmp_path) -> None:
    store = FaultStateStore(tmp_path)
    first = acquire(store)
    with pytest.raises(RuntimeError, match="already active"):
        acquire(store, "fault-run-2")
    store.release(first.activation_id)
    assert store.active() is None


def test_store_rejects_release_by_non_owner(tmp_path) -> None:
    store = FaultStateStore(tmp_path)
    acquire(store)
    with pytest.raises(RuntimeError, match="another activation"):
        store.release("0" * 32)


def test_expired_fault_is_removed(tmp_path) -> None:
    store = FaultStateStore(tmp_path)
    activation = acquire(store)
    expired = activation.model_copy(update={"expires_at": utc_now() - timedelta(seconds=1)})
    store.active_path.write_text(expired.model_dump_json(), encoding="utf-8")
    assert store.active() is None
    assert not store.active_path.exists()


def test_lease_cleans_up_and_verifies_recovery_after_body_error(tmp_path) -> None:
    store = FaultStateStore(tmp_path)
    activation = acquire(store)
    calls: list[str] = []
    with pytest.raises(ValueError, match="experiment failed"):
        with FaultLease(
            store, activation, lambda _: calls.append("activate"),
            lambda _: calls.append("deactivate"), lambda: calls.append("verify"),
        ):
            raise ValueError("experiment failed")
    assert calls == ["activate", "deactivate", "verify"]
    assert store.active() is None


def test_lease_releases_state_when_activation_hook_fails(tmp_path) -> None:
    store = FaultStateStore(tmp_path)
    activation = acquire(store)

    def fail(_):
        raise RuntimeError("activation failed")

    with pytest.raises(RuntimeError, match="activation failed"):
        with FaultLease(store, activation, fail, lambda _: None, lambda: None):
            pass
    assert store.active() is None
