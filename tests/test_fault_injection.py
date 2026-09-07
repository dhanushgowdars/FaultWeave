import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from faultweave_common import fault_injection
from faultweave_common.logging import reset_correlation_context, set_correlation_context


def write_active(tmp_path, **updates) -> None:
    payload = {
        "run_id": "controlled-run", "fault_id": "DATABASE_HIGH_LATENCY",
        "target": "transaction", "intensity": "mild",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }
    payload.update(updates)
    (tmp_path / "active_fault.json").write_text(json.dumps(payload), encoding="utf-8")


def test_database_latency_applies_only_to_matching_run_and_service(tmp_path, monkeypatch) -> None:
    write_active(tmp_path)
    monkeypatch.setattr(fault_injection, "CONTROL_PATH", tmp_path / "active_fault.json")
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(fault_injection.asyncio, "sleep", fake_sleep)
    tokens = set_correlation_context("controlled-run", "request-1", "trace-1")
    try:
        asyncio.run(fault_injection.apply_database_latency("transaction"))
        asyncio.run(fault_injection.apply_database_latency("payment"))
    finally:
        reset_correlation_context(tokens)
    assert calls == [0.25]


def test_downstream_timeout_raises_for_exact_dependency_edge(tmp_path, monkeypatch) -> None:
    write_active(
        tmp_path, fault_id="DOWNSTREAM_TIMEOUT",
        target="transaction->payment", intensity="medium",
    )
    monkeypatch.setattr(fault_injection, "CONTROL_PATH", tmp_path / "active_fault.json")

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(fault_injection.asyncio, "sleep", fake_sleep)
    tokens = set_correlation_context("controlled-run", "request-1", "trace-1")
    try:
        with pytest.raises(httpx.ReadTimeout):
            asyncio.run(
                fault_injection.apply_downstream_timeout(
                    "transaction", "payment", "http://payment-service/internal"
                )
            )
    finally:
        reset_correlation_context(tokens)


def test_expired_or_different_run_fault_is_ignored(tmp_path, monkeypatch) -> None:
    write_active(tmp_path, run_id="another-run")
    monkeypatch.setattr(fault_injection, "CONTROL_PATH", tmp_path / "active_fault.json")
    tokens = set_correlation_context("controlled-run", "request-1", "trace-1")
    try:
        assert fault_injection.active_fault() is None
    finally:
        reset_correlation_context(tokens)
