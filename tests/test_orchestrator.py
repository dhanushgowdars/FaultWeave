from __future__ import annotations

from uuid import UUID

import httpx
import pytest
from app.orchestrator import TransactionOrchestrator
from fastapi import HTTPException
from faultweave_common.schemas import TransactionRequest, TransactionStatus

ACCOUNT_ID = UUID("44444444-4444-4444-8444-444444444444")
TRANSACTION_ID = UUID("11111111-1111-4111-8111-111111111111")
PAYMENT_ID = UUID("22222222-2222-4222-8222-222222222222")
LEDGER_ID = UUID("55555555-5555-4555-8555-555555555555")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")


def request_payload() -> TransactionRequest:
    return TransactionRequest(
        username="demo",
        password="faultweave-demo",
        account_number="FW-DEMO-001",
        amount_minor=12500,
        currency="INR",
        recipient="merchant-demo",
    )


@pytest.mark.anyio
async def test_orchestrator_calls_frozen_gateway_dependencies_and_propagates_ids() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.headers["X-Run-ID"] == "run-001"
        assert request.headers["X-Request-ID"] == "flow-001"
        assert request.headers["X-Trace-ID"] == "trace-001"
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(
                200,
                json={
                    "access_token": "signed-demo-token",
                    "token_type": "bearer",
                    "expires_in": 900,
                    "user_id": str(USER_ID),
                },
            )
        assert request.headers["Authorization"] == "Bearer signed-demo-token"
        assert request.headers["X-Service-Token"]
        if request.url.path.endswith("/accounts/lookup"):
            return httpx.Response(
                200,
                json={
                    "id": str(ACCOUNT_ID),
                    "user_id": str(USER_ID),
                    "account_number": "FW-DEMO-001",
                    "currency": "INR",
                    "balance_minor": 100_000_000,
                    "is_active": True,
                    "created_at": "2026-09-06T00:00:00Z",
                },
            )
        return httpx.Response(
            200,
            json={
                "request_id": "flow-001",
                "account_id": str(ACCOUNT_ID),
                "transaction_id": str(TRANSACTION_ID),
                "payment_id": str(PAYMENT_ID),
                "ledger_entry_id": str(LEDGER_ID),
                "status": "COMPLETED",
                "message": "Simulated transaction completed successfully",
            },
        )

    orchestrator = TransactionOrchestrator(transport=httpx.MockTransport(handler))
    result = await orchestrator.run(
        request_payload(),
        "flow-001",
        {"X-Run-ID": "run-001", "X-Request-ID": "flow-001", "X-Trace-ID": "trace-001"},
    )

    assert result.status == TransactionStatus.COMPLETED
    assert result.transaction_id == TRANSACTION_ID
    assert result.payment_id == PAYMENT_ID
    assert calls == [
        "POST /internal/v1/auth/login",
        "POST /internal/v1/accounts/lookup",
        "POST /internal/v1/transactions/process",
    ]


@pytest.mark.anyio
async def test_orchestrator_preserves_authentication_failure() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(401, json={"detail": "Invalid credentials"})
    )
    orchestrator = TransactionOrchestrator(transport=transport)

    with pytest.raises(HTTPException) as captured:
        await orchestrator.run(request_payload(), "flow-unauthorized")

    assert captured.value.status_code == 401
    assert captured.value.detail == "Invalid credentials"
