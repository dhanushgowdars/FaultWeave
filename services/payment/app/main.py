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
    HealthResponse,
    LedgerEntryCreate,
    LedgerEntryRecord,
    LedgerEntryType,
    PaymentCreate,
    PaymentRecord,
    PaymentStatus,
)
from faultweave_common.security import authenticated_subject, require_service_token
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Base, Payment

engine = make_engine()
session_factory = make_session_factory(engine)
logger = configure_logging("payment")
downstream = DownstreamClient(logger)
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


app = FastAPI(title="FaultWeave Payment Service", version="0.2.0", lifespan=lifespan)
app.add_middleware(CorrelationMiddleware, service="payment")


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="payment", status="up")


@app.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    if not await database_ready(engine):
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(service="payment", status="up", database="up")


@app.post(
    "/internal/v1/payments",
    response_model=PaymentRecord,
    dependencies=[Depends(require_service_token)],
)
async def create_payment(
    payload: PaymentCreate,
    authorization: str = Header(...),
    _: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> Payment:
    payment_id = uuid4()
    ledger_payload = LedgerEntryCreate(
        transaction_id=payload.transaction_id,
        payment_id=payment_id,
        account_id=payload.account_id,
        entry_type=LedgerEntryType.PAYMENT_COMPLETED,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        request_id=payload.request_id,
    )
    async with httpx.AsyncClient(timeout=5.0) as client:
        ledger_response = await downstream.request(
            client,
            "POST",
            f"{LEDGER_SERVICE_URL}/internal/v1/ledger/entries",
            "ledger",
            payload=ledger_payload.model_dump(mode="json"),
            headers={"Authorization": authorization, "X-Service-Token": SERVICE_TOKEN},
        )
    ledger_entry = LedgerEntryRecord.model_validate(ledger_response.json())

    record = Payment(
        id=str(payment_id),
        transaction_id=str(payload.transaction_id),
        account_id=str(payload.account_id),
        request_id=payload.request_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        status=PaymentStatus.COMPLETED.value,
        provider_reference=f"sim_{uuid4().hex[:20]}",
        ledger_entry_id=str(ledger_entry.id),
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    logger.info(
        "payment_completed",
        "Simulated payment completed",
        outcome=LogOutcome.SUCCESS,
        transaction_id=record.transaction_id,
        payment_id=record.id,
        attributes={
            "payment_status": record.status,
            "ledger_entry_id": record.ledger_entry_id,
        },
    )
    return record
