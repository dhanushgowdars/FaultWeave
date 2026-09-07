import asyncio

import httpx
import pytest
from faultweave_common import fault_injection
from faultweave_common.logging import reset_correlation_context, set_correlation_context


def unknown_payload(fault_id: str, target: str, intensity: str = "medium") -> dict:
    return {
        "experiment_scope": "sealed_unknown_evaluation",
        "fault_id": fault_id,
        "target": target,
        "intensity": intensity,
    }


def test_intermittent_connection_failure_is_partial_and_edge_scoped(monkeypatch) -> None:
    monkeypatch.setattr(
        fault_injection,
        "active_fault",
        lambda: unknown_payload(
            "INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE", "transaction->payment"
        ),
    )
    failures = 0
    successes = 0
    for index in range(40):
        tokens = set_correlation_context("unknown-run", f"request-{index}", "trace")
        try:
            try:
                asyncio.run(
                    fault_injection.apply_sealed_unknown_downstream_disruption(
                        "transaction", "payment", "http://payment/internal"
                    )
                )
                successes += 1
            except httpx.ConnectError:
                failures += 1
        finally:
            reset_correlation_context(tokens)
    assert failures > 0
    assert successes > 0


def test_latency_jitter_has_multiple_bands_and_excludes_health(monkeypatch) -> None:
    monkeypatch.setattr(
        fault_injection,
        "active_fault",
        lambda: unknown_payload("LATENCY_JITTER_PARTIAL_DEGRADATION", "transaction", "high"),
    )
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(fault_injection.asyncio, "sleep", fake_sleep)
    for index in range(60):
        tokens = set_correlation_context("unknown-run", f"request-{index}", "trace")
        try:
            asyncio.run(
                fault_injection.apply_sealed_unknown_latency_jitter(
                    "transaction", "/internal/v1/transactions/process"
                )
            )
        finally:
            reset_correlation_context(tokens)
    assert set(delays) == {0.45, 1.5}
    before_health = len(delays)
    tokens = set_correlation_context("unknown-run", "health-request", "trace")
    try:
        asyncio.run(
            fault_injection.apply_sealed_unknown_latency_jitter(
                "transaction", "/health/ready"
            )
        )
    finally:
        reset_correlation_context(tokens)
    assert len(delays) == before_health


def test_unknown_injectors_require_evaluation_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        fault_injection,
        "active_fault",
        lambda: {
            "fault_id": "LATENCY_JITTER_PARTIAL_DEGRADATION",
            "target": "transaction",
            "intensity": "high",
        },
    )

    async def fail_sleep(_: float) -> None:
        pytest.fail("unsealed activation must not inject")

    monkeypatch.setattr(fault_injection.asyncio, "sleep", fail_sleep)
    asyncio.run(
        fault_injection.apply_sealed_unknown_latency_jitter("transaction", "/internal")
    )
