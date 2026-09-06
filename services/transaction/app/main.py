from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from faultweave_common.db import database_ready, make_engine, make_session_factory
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import RequestIdMiddleware
from faultweave_common.schemas import (
    HealthResponse,
    TransactionComplete,
    TransactionCreate,
    TransactionRecord,
    TransactionStatus,
)
from faultweave_common.security import authenticated_subject, require_service_token
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Base, Transaction

engine = make_engine()
session_factory = make_session_factory(engine)
logger = configure_logging("transaction")


async def get_session():
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="FaultWeave Transaction Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware, service="transaction")


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="transaction", status="up")


@app.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    if not await database_ready(engine):
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(service="transaction", status="up", database="up")


@app.post(
    "/internal/v1/transactions",
    response_model=TransactionRecord,
    dependencies=[Depends(require_service_token)],
)
async def create_transaction(
    payload: TransactionCreate,
    user_id: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> Transaction:
    record = Transaction(
        user_id=user_id,
        request_id=payload.request_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        recipient=payload.recipient,
        status=TransactionStatus.PENDING.value,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    logger.info(
        "transaction_created",
        "Transaction record created",
        outcome=LogOutcome.SUCCESS,
        user_id=user_id,
        transaction_id=record.id,
        attributes={"transaction_status": record.status},
    )
    return record


@app.patch(
    "/internal/v1/transactions/{transaction_id}/complete",
    response_model=TransactionRecord,
    dependencies=[Depends(require_service_token)],
)
async def complete_transaction(
    transaction_id: UUID,
    payload: TransactionComplete,
    _: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> Transaction:
    record = await session.get(Transaction, str(transaction_id))
    if record is None:
        logger.warning(
            "transaction_not_found",
            "Transaction completion target was not found",
            outcome=LogOutcome.FAILURE,
            transaction_id=str(transaction_id),
            error_type="TransactionNotFound",
        )
        raise HTTPException(status_code=404, detail="Transaction not found")
    if record.status != TransactionStatus.PENDING.value:
        logger.warning(
            "transaction_state_conflict",
            "Transaction was not in a completable state",
            outcome=LogOutcome.FAILURE,
            transaction_id=record.id,
            error_type="TransactionStateConflict",
            attributes={"transaction_status": record.status},
        )
        raise HTTPException(status_code=409, detail="Transaction is not pending")
    record.status = TransactionStatus.COMPLETED.value
    record.payment_id = str(payload.payment_id)
    await session.commit()
    await session.refresh(record)
    logger.info(
        "transaction_completed",
        "Transaction completed after simulated payment",
        outcome=LogOutcome.SUCCESS,
        transaction_id=record.id,
        payment_id=record.payment_id,
        attributes={"transaction_status": record.status},
    )
    return record
