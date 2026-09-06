from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from faultweave_common.db import database_ready, make_engine, make_session_factory
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import RequestIdMiddleware
from faultweave_common.schemas import HealthResponse, PaymentCreate, PaymentRecord, PaymentStatus
from faultweave_common.security import authenticated_subject, require_service_token
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Base, Payment

engine = make_engine()
session_factory = make_session_factory(engine)
logger = configure_logging("payment")


async def get_session():
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="FaultWeave Payment Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware, service="payment")


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
    _: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> Payment:
    record = Payment(
        transaction_id=str(payload.transaction_id),
        request_id=payload.request_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        status=PaymentStatus.COMPLETED.value,
        provider_reference=f"sim_{uuid4().hex[:20]}",
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
        attributes={"payment_status": record.status},
    )
    return record
