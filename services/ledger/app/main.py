from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from faultweave_common.db import (
    database_ready,
    ensure_schema,
    make_engine,
    make_session_factory,
)
from faultweave_common.fault_injection import apply_database_latency
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import CorrelationMiddleware
from faultweave_common.schemas import HealthResponse, LedgerEntryCreate, LedgerEntryRecord
from faultweave_common.security import authenticated_subject, require_service_token
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Base, LedgerEntry

engine = make_engine()
session_factory = make_session_factory(engine)
logger = configure_logging("ledger")


async def get_session():
    await apply_database_latency("ledger")
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def lifespan(_: FastAPI):
    await ensure_schema(engine, "ledger")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="FaultWeave Ledger Service", version="0.2.0", lifespan=lifespan)
app.add_middleware(CorrelationMiddleware, service="ledger")


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="ledger", status="up")


@app.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    if not await database_ready(engine):
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(service="ledger", status="up", database="up")


@app.post(
    "/internal/v1/ledger/entries",
    response_model=LedgerEntryRecord,
    dependencies=[Depends(require_service_token)],
)
async def create_entry(
    payload: LedgerEntryCreate,
    _: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntry:
    entry = LedgerEntry(
        transaction_id=str(payload.transaction_id),
        payment_id=str(payload.payment_id) if payload.payment_id else None,
        account_id=str(payload.account_id),
        entry_type=payload.entry_type.value,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        request_id=payload.request_id,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    logger.info(
        "ledger_entry_created",
        "Immutable ledger entry created",
        outcome=LogOutcome.SUCCESS,
        transaction_id=entry.transaction_id,
        payment_id=entry.payment_id,
        attributes={"account_id": entry.account_id, "entry_type": entry.entry_type},
    )
    return entry
