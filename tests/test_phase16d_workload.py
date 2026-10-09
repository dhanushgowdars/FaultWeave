import asyncio

import httpx

from scripts.verify_phase16d import (
    reconcile_pending_rows,
    reconcile_transfer,
    request_observation,
    validate_fault_phase,
    validate_phase,
)


def row(state: str) -> dict:
    return {
        "request_id": "request-1",
        "trace_id": "trace-1",
        "started_at": "2026-10-09T03:00:00Z",
        "latency_ms": 25.0,
        "status_code": 200,
        "expected_outcome": state == "COMPLETED",
        "transport_error": None,
        "state": state,
    }


def test_request_observation_preserves_client_observed_business_outcome() -> None:
    observation = request_observation(row("REQUIRES_REVIEW"))

    assert observation["status_code"] == 200
    assert observation["expected_outcome"] is False
    assert observation["scenario"] == "valid"


def test_phase_validation_requires_expected_state_rate() -> None:
    validate_phase(
        "fault",
        [row("REQUIRES_REVIEW")] * 8 + [row("COMPLETED")] * 2,
        "REQUIRES_REVIEW",
        minimum_rate=0.8,
    )


def test_reconciliation_reads_durable_transfer_after_uncertain_response() -> None:
    transfer = {"id": "transfer-1", "state": "REQUIRES_REVIEW"}

    async def scenario() -> dict:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/api/v1/transfers/by-key/stable-key"
            return httpx.Response(200, json=transfer)

        async with httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(handler)
        ) as client:
            return await reconcile_transfer(
                client,
                idempotency_key="stable-key",
                payload={"idempotency_key": "stable-key"},
                attempts=1,
            )

    assert asyncio.run(scenario()) == transfer


def test_reconciliation_replays_only_same_identity_when_receipt_is_absent() -> None:
    calls: list[tuple[str, str]] = []
    transfer = {"id": "transfer-2", "state": "COMPLETED"}
    payload = {"idempotency_key": "stable-key"}

    async def scenario() -> dict:
        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(404, json={"detail": "Transfer not found"})
            assert request.read()
            return httpx.Response(200, json=transfer)

        async with httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(handler)
        ) as client:
            return await reconcile_transfer(
                client,
                idempotency_key="stable-key",
                payload=payload,
                attempts=1,
            )

    assert asyncio.run(scenario()) == transfer
    assert calls == [
        ("GET", "/api/v1/transfers/by-key/stable-key"),
        ("POST", "/api/v1/transfers"),
    ]


def test_pending_fault_request_is_reconciled_only_when_explicitly_called() -> None:
    calls: list[tuple[str, str]] = []
    pending = {
        "idempotency_key": "stable-key",
        "payload": {"idempotency_key": "stable-key"},
    }
    rows = [{"state": None, "pending_reconciliation": pending}]

    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            return httpx.Response(
                200,
                json={"id": "transfer-3", "state": "REQUIRES_REVIEW"},
            )

        async with httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(handler)
        ) as client:
            # During the outage, the original 502 is observed and retained; no
            # reconciliation request is sent until the caller declares recovery.
            assert calls == []
            assert rows[0]["pending_reconciliation"] == pending
            await reconcile_pending_rows(client, rows)

    asyncio.run(scenario())
    assert calls == [("GET", "/api/v1/transfers/by-key/stable-key")]
    assert rows == [{"state": "REQUIRES_REVIEW", "transfer_id": "transfer-3"}]


def test_fault_validation_counts_durable_reviews_and_real_gateway_failures() -> None:
    reviewed = [row("REQUIRES_REVIEW") for _ in range(18)]
    gateway_failures = [
        {**row("COMPLETED"), "status_code": 502, "expected_outcome": False}
        for _ in range(57)
    ]

    result = validate_fault_phase([*reviewed, *gateway_failures])

    assert result["affected_rate"] == 1.0
    assert result["requires_review"] == 18
    assert result["requires_review_rate"] == 0.24
    assert result["server_errors"] == 57


def test_fault_validation_rejects_only_gateway_errors_without_review_evidence() -> None:
    gateway_failures = [
        {**row("COMPLETED"), "status_code": 502, "expected_outcome": False}
        for _ in range(10)
    ]

    try:
        validate_fault_phase(gateway_failures)
    except RuntimeError as exc:
        assert "durable REQUIRES_REVIEW" in str(exc)
    else:
        raise AssertionError("fault phase without durable review evidence was accepted")
