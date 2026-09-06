from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from app.orchestrator import TransactionOrchestrator
from fastapi import HTTPException
from faultweave_common.schemas import TransactionRequest, TransactionStatus

TRANSACTION_ID = UUID("11111111-1111-4111-8111-111111111111")
PAYMENT_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")


def request_payload() -> TransactionRequest:
    return TransactionRequest(
        username="demo",
        password="faultweave-demo",
        amount_minor=12500,
        currency="INR",
        recipient="merchant-demo",
    )


@pytest.mark.anyio
async def test_orchestrator_calls_services_in_order_and_propagates_request_id() -> None:
    calls: list[str] = []
    now = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.headers["X-Request-ID"] == "flow-001"
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
        if request.method == "POST" and request.url.path.endswith("/transactions"):
            return httpx.Response(
                200,
                json={
                    "id": str(TRANSACTION_ID),
                    "user_id": str(USER_ID),
                    "request_id": "flow-001",
                    "amount_minor": 12500,
                    "currency": "INR",
                    "recipient": "merchant-demo",
                    "status": "PENDING",
                    "payment_id": None,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        if request.url.path.endswith("/payments"):
            return httpx.Response(
                200,
                json={
                    "id": str(PAYMENT_ID),
                    "transaction_id": str(TRANSACTION_ID),
                    "request_id": "flow-001",
                    "amount_minor": 12500,
                    "currency": "INR",
                    "status": "COMPLETED",
                    "provider_reference": "sim_test_reference",
                    "created_at": now,
                },
            )
        return httpx.Response(
            200,
            json={
                "id": str(TRANSACTION_ID),
                "user_id": str(USER_ID),
                "request_id": "flow-001",
                "amount_minor": 12500,
                "currency": "INR",
                "recipient": "merchant-demo",
                "status": "COMPLETED",
                "payment_id": str(PAYMENT_ID),
                "created_at": now,
                "updated_at": now,
            },
        )

    orchestrator = TransactionOrchestrator(transport=httpx.MockTransport(handler))
    result = await orchestrator.run(request_payload(), "flow-001")

    assert result.status == TransactionStatus.COMPLETED
    assert result.transaction_id == TRANSACTION_ID
    assert result.payment_id == PAYMENT_ID
    assert calls == [
        "POST /internal/v1/auth/login",
        "POST /internal/v1/transactions",
        "POST /internal/v1/payments",
        f"PATCH /internal/v1/transactions/{TRANSACTION_ID}/complete",
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
