from __future__ import annotations

import os

import httpx
from fastapi import HTTPException
from faultweave_common.config import SERVICE_TOKEN
from faultweave_common.schemas import (
    LoginRequest,
    LoginResponse,
    PaymentCreate,
    PaymentRecord,
    TransactionComplete,
    TransactionCreate,
    TransactionFlowResponse,
    TransactionRecord,
    TransactionRequest,
)


class TransactionOrchestrator:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.auth_url = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8000")
        self.transaction_url = os.getenv(
            "TRANSACTION_SERVICE_URL", "http://transaction-service:8000"
        )
        self.payment_url = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8000")
        self.transport = transport

    async def run(self, payload: TransactionRequest, request_id: str) -> TransactionFlowResponse:
        headers = {"X-Request-ID": request_id, "X-Service-Token": SERVICE_TOKEN}
        try:
            async with httpx.AsyncClient(timeout=5.0, transport=self.transport) as client:
                login_response = await client.post(
                    f"{self.auth_url}/internal/v1/auth/login",
                    json=LoginRequest(
                        username=payload.username,
                        password=payload.password,
                    ).model_dump(),
                    headers={"X-Request-ID": request_id},
                )
                login_response.raise_for_status()
                login = LoginResponse.model_validate(login_response.json())
                headers["Authorization"] = f"Bearer {login.access_token}"

                transaction_response = await client.post(
                    f"{self.transaction_url}/internal/v1/transactions",
                    json=TransactionCreate(
                        amount_minor=payload.amount_minor,
                        currency=payload.currency,
                        recipient=payload.recipient,
                        request_id=request_id,
                    ).model_dump(mode="json"),
                    headers=headers,
                )
                transaction_response.raise_for_status()
                transaction = TransactionRecord.model_validate(transaction_response.json())

                payment_response = await client.post(
                    f"{self.payment_url}/internal/v1/payments",
                    json=PaymentCreate(
                        transaction_id=transaction.id,
                        amount_minor=payload.amount_minor,
                        currency=payload.currency,
                        request_id=request_id,
                    ).model_dump(mode="json"),
                    headers=headers,
                )
                payment_response.raise_for_status()
                payment = PaymentRecord.model_validate(payment_response.json())

                completed_response = await client.patch(
                    f"{self.transaction_url}/internal/v1/transactions/{transaction.id}/complete",
                    json=TransactionComplete(payment_id=payment.id).model_dump(mode="json"),
                    headers=headers,
                )
                completed_response.raise_for_status()
                completed = TransactionRecord.model_validate(completed_response.json())
        except httpx.HTTPStatusError as exc:
            detail = "A downstream service rejected the transaction"
            if exc.response.status_code == 401:
                raise HTTPException(status_code=401, detail="Invalid credentials") from exc
            raise HTTPException(status_code=502, detail=detail) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail="A downstream service is unavailable",
            ) from exc

        return TransactionFlowResponse(
            request_id=request_id,
            transaction_id=completed.id,
            payment_id=payment.id,
            status=completed.status,
            message="Simulated transaction completed successfully",
        )
