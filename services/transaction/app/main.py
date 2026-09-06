from __future__ import annotations

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from faultweave_common.config import SERVICE_TOKEN
from faultweave_common.db import database_ready, make_engine, make_session_factory
from faultweave_common.http import DownstreamClient
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import CorrelationMiddleware
from faultweave_common.schemas import (
    AccountValidation,
    AccountValidationResult,
    HealthResponse,
    LedgerEntryCreate,
    LedgerEntryRecord,
    LedgerEntryType,
    PaymentCreate,
    PaymentRecord,
    TransactionFlowResponse,
    TransactionProcess,
    TransactionStatus,
)
from faultweave_common.security import authenticated_subject, require_service_token
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Base, Transaction

engine = make_engine()
session_factory = make_session_factory(engine)
logger = configure_logging("transaction")
downstream = DownstreamClient(logger)
ACCOUNT_SERVICE_URL = os.getenv("ACCOUNT_SERVICE_URL", "http://account-service:8000")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8000")
LEDGER_SERVICE_URL = os.getenv("LEDGER_SERVICE_URL", "http://ledger-service:8000")


async def get_session():
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="FaultWeave Transaction Service", version="0.2.0", lifespan=lifespan)
app.add_middleware(CorrelationMiddleware, service="transaction")


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="transaction", status="up")


@app.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    if not await database_ready(engine):
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(service="transaction", status="up", database="up")


@app.post(
    "/internal/v1/transactions/process",
    response_model=TransactionFlowResponse,
    dependencies=[Depends(require_service_token)],
)
async def process_transaction(
    payload: TransactionProcess,
    authorization: str = Header(...),
    user_id: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> TransactionFlowResponse:
    service_headers = {
        "Authorization": authorization,
        "X-Service-Token": SERVICE_TOKEN,
    }
    transaction = Transaction(
        id=str(uuid4()),
        user_id=user_id,
        account_id=str(payload.account_id),
        request_id=payload.request_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        recipient=payload.recipient,
        status=TransactionStatus.PENDING.value,
    )
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    logger.info(
        "transaction_created",
        "Transaction record created",
        outcome=LogOutcome.SUCCESS,
        user_id=user_id,
        transaction_id=transaction.id,
        attributes={"account_id": transaction.account_id, "transaction_status": transaction.status},
    )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            account_response = await downstream.request(
                client,
                "POST",
                f"{ACCOUNT_SERVICE_URL}/internal/v1/accounts/validate",
                "account",
                payload=AccountValidation(
                    account_id=payload.account_id,
                    amount_minor=payload.amount_minor,
                    currency=payload.currency,
                ).model_dump(mode="json"),
                headers=service_headers,
            )
            validation = AccountValidationResult.model_validate(account_response.json())
            if not validation.valid:
                raise HTTPException(status_code=409, detail=validation.reason)

            payment_response = await downstream.request(
                client,
                "POST",
                f"{PAYMENT_SERVICE_URL}/internal/v1/payments",
                "payment",
                payload=PaymentCreate(
                    transaction_id=transaction.id,
                    account_id=payload.account_id,
                    amount_minor=payload.amount_minor,
                    currency=payload.currency,
                    request_id=payload.request_id,
                ).model_dump(mode="json"),
                headers=service_headers,
            )
            payment = PaymentRecord.model_validate(payment_response.json())

            ledger_response = await downstream.request(
                client,
                "POST",
                f"{LEDGER_SERVICE_URL}/internal/v1/ledger/entries",
                "ledger",
                payload=LedgerEntryCreate(
                    transaction_id=transaction.id,
                    payment_id=payment.id,
                    account_id=payload.account_id,
                    entry_type=LedgerEntryType.TRANSACTION_COMPLETED,
                    amount_minor=payload.amount_minor,
                    currency=payload.currency,
                    request_id=payload.request_id,
                ).model_dump(mode="json"),
                headers=service_headers,
            )
            ledger_entry = LedgerEntryRecord.model_validate(ledger_response.json())
    except HTTPException:
        transaction.status = TransactionStatus.FAILED.value
        await session.commit()
        logger.warning(
            "transaction_failed",
            "Transaction failed account validation",
            outcome=LogOutcome.FAILURE,
            transaction_id=transaction.id,
            error_type="AccountValidationFailure",
        )
        raise
    except httpx.HTTPStatusError as exc:
        transaction.status = TransactionStatus.FAILED.value
        await session.commit()
        logger.error(
            "transaction_failed",
            "Transaction failed because a downstream service rejected the request",
            outcome=LogOutcome.FAILURE,
            transaction_id=transaction.id,
            error_type="DownstreamHTTPError",
        )
        raise HTTPException(status_code=502, detail="Transaction dependency failed") from exc
    except httpx.RequestError as exc:
        transaction.status = TransactionStatus.FAILED.value
        await session.commit()
        logger.error(
            "transaction_failed",
            "Transaction failed because a downstream service was unavailable",
            outcome=LogOutcome.FAILURE,
            transaction_id=transaction.id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="Transaction dependency unavailable") from exc

    transaction.status = TransactionStatus.COMPLETED.value
    transaction.payment_id = str(payment.id)
    transaction.ledger_entry_id = str(ledger_entry.id)
    await session.commit()
    await session.refresh(transaction)
    logger.info(
        "transaction_completed",
        "Transaction completed with payment and ledger evidence",
        outcome=LogOutcome.SUCCESS,
        transaction_id=transaction.id,
        payment_id=transaction.payment_id,
        attributes={
            "account_id": transaction.account_id,
            "ledger_entry_id": transaction.ledger_entry_id,
            "transaction_status": transaction.status,
        },
    )
    return TransactionFlowResponse(
        request_id=payload.request_id,
        account_id=payload.account_id,
        transaction_id=transaction.id,
        payment_id=payment.id,
        ledger_entry_id=ledger_entry.id,
        status=TransactionStatus.COMPLETED,
        message="Simulated transaction completed successfully",
    )
