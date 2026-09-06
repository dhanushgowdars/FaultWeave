from __future__ import annotations

import os
from time import perf_counter
from typing import Any

import httpx
from fastapi import HTTPException
from faultweave_common.config import SERVICE_TOKEN
from faultweave_common.logging import LogOutcome, configure_logging
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

logger = configure_logging("gateway")


class TransactionOrchestrator:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.auth_url = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8000")
        self.transaction_url = os.getenv(
            "TRANSACTION_SERVICE_URL", "http://transaction-service:8000"
        )
        self.payment_url = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8000")
        self.transport = transport

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        downstream_service: str,
        request_id: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        started_at = perf_counter()
        try:
            response = await client.request(method, url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            logger.error(
                "downstream_request_failed",
                "Downstream service request failed before receiving a response",
                request_id=request_id,
                method=method,
                path=httpx.URL(url).path,
                latency_ms=round((perf_counter() - started_at) * 1000, 3),
                outcome=LogOutcome.FAILURE,
                downstream_service=downstream_service,
                error_type=type(exc).__name__,
            )
            raise

        status_code = response.status_code
        log_method = logger.info
        outcome = LogOutcome.SUCCESS
        if status_code >= 500:
            log_method = logger.error
            outcome = LogOutcome.FAILURE
        elif status_code >= 400:
            log_method = logger.warning
            outcome = LogOutcome.FAILURE
        log_method(
            "downstream_request_completed",
            "Downstream service request completed",
            request_id=request_id,
            method=method,
            path=httpx.URL(url).path,
            status_code=status_code,
            latency_ms=round((perf_counter() - started_at) * 1000, 3),
            outcome=outcome,
            downstream_service=downstream_service,
        )
        response.raise_for_status()
        return response

    async def run(self, payload: TransactionRequest, request_id: str) -> TransactionFlowResponse:
        headers = {"X-Request-ID": request_id, "X-Service-Token": SERVICE_TOKEN}
        try:
            async with httpx.AsyncClient(timeout=5.0, transport=self.transport) as client:
                login_response = await self._request(
                    client,
                    "POST",
                    f"{self.auth_url}/internal/v1/auth/login",
                    "authentication",
                    request_id,
                    payload=LoginRequest(
                        username=payload.username,
                        password=payload.password,
                    ).model_dump(),
                    headers={"X-Request-ID": request_id},
                )
                login = LoginResponse.model_validate(login_response.json())
                headers["Authorization"] = f"Bearer {login.access_token}"

                transaction_response = await self._request(
                    client,
                    "POST",
                    f"{self.transaction_url}/internal/v1/transactions",
                    "transaction",
                    request_id,
                    payload=TransactionCreate(
                        amount_minor=payload.amount_minor,
                        currency=payload.currency,
                        recipient=payload.recipient,
                        request_id=request_id,
                    ).model_dump(mode="json"),
                    headers=headers,
                )
                transaction = TransactionRecord.model_validate(transaction_response.json())

                payment_response = await self._request(
                    client,
                    "POST",
                    f"{self.payment_url}/internal/v1/payments",
                    "payment",
                    request_id,
                    payload=PaymentCreate(
                        transaction_id=transaction.id,
                        amount_minor=payload.amount_minor,
                        currency=payload.currency,
                        request_id=request_id,
                    ).model_dump(mode="json"),
                    headers=headers,
                )
                payment = PaymentRecord.model_validate(payment_response.json())

                completed_response = await self._request(
                    client,
                    "PATCH",
                    f"{self.transaction_url}/internal/v1/transactions/{transaction.id}/complete",
                    "transaction",
                    request_id,
                    payload=TransactionComplete(payment_id=payment.id).model_dump(mode="json"),
                    headers=headers,
                )
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
