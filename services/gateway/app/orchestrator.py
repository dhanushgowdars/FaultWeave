from __future__ import annotations

import os

import httpx
from fastapi import HTTPException
from faultweave_common.config import SERVICE_TOKEN
from faultweave_common.http import DownstreamClient
from faultweave_common.logging import configure_logging
from faultweave_common.schemas import (
    AccountLookup,
    AccountRecord,
    LoginRequest,
    LoginResponse,
    TransactionFlowResponse,
    TransactionProcess,
    TransactionRequest,
)

logger = configure_logging("gateway")


class TransactionOrchestrator:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.auth_url = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8000")
        self.account_url = os.getenv("ACCOUNT_SERVICE_URL", "http://account-service:8000")
        self.transaction_url = os.getenv(
            "TRANSACTION_SERVICE_URL", "http://transaction-service:8000"
        )
        self.transport = transport
        self.downstream = DownstreamClient(logger, transport)

    async def run(
        self,
        payload: TransactionRequest,
        request_id: str,
        correlation: dict[str, str] | None = None,
    ) -> TransactionFlowResponse:
        correlated_headers = correlation or {"X-Request-ID": request_id}
        try:
            async with httpx.AsyncClient(timeout=5.0, transport=self.transport) as client:
                login_response = await self.downstream.request(
                    client,
                    "POST",
                    f"{self.auth_url}/internal/v1/auth/login",
                    "authentication",
                    payload=LoginRequest(
                        username=payload.username,
                        password=payload.password,
                    ).model_dump(),
                    headers=correlated_headers,
                )
                login = LoginResponse.model_validate(login_response.json())
                service_headers = {
                    **correlated_headers,
                    "Authorization": f"Bearer {login.access_token}",
                    "X-Service-Token": SERVICE_TOKEN,
                }

                account_response = await self.downstream.request(
                    client,
                    "POST",
                    f"{self.account_url}/internal/v1/accounts/lookup",
                    "account",
                    payload=AccountLookup(account_number=payload.account_number).model_dump(),
                    headers=service_headers,
                )
                account = AccountRecord.model_validate(account_response.json())

                transaction_response = await self.downstream.request(
                    client,
                    "POST",
                    f"{self.transaction_url}/internal/v1/transactions/process",
                    "transaction",
                    payload=TransactionProcess(
                        account_id=account.id,
                        amount_minor=payload.amount_minor,
                        currency=payload.currency,
                        recipient=payload.recipient,
                        request_id=request_id,
                    ).model_dump(mode="json"),
                    headers=service_headers,
                )
                return TransactionFlowResponse.model_validate(transaction_response.json())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise HTTPException(status_code=401, detail="Invalid credentials") from exc
            if exc.response.status_code in {400, 404, 409, 422}:
                detail = exc.response.json().get("detail", "Transaction request rejected")
                raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
            raise HTTPException(
                status_code=502,
                detail="A downstream service rejected the transaction",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail="A downstream service is unavailable",
            ) from exc
