from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from faultweave_common.db import (
    database_ready,
    ensure_schema,
    make_engine,
    make_session_factory,
)
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import CorrelationMiddleware
from faultweave_common.schemas import (
    AccountLookup,
    AccountRecord,
    AccountValidation,
    AccountValidationResult,
    HealthResponse,
)
from faultweave_common.security import authenticated_subject, require_service_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Account, Base

engine = make_engine()
session_factory = make_session_factory(engine)
logger = configure_logging("account")
DEMO_ACCOUNT_NUMBER = os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001")


async def get_session():
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def lifespan(_: FastAPI):
    await ensure_schema(engine, "accounts")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="FaultWeave Account Service", version="0.2.0", lifespan=lifespan)
app.add_middleware(CorrelationMiddleware, service="account")


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="account", status="up")


@app.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    if not await database_ready(engine):
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(service="account", status="up", database="up")


@app.post(
    "/internal/v1/accounts/lookup",
    response_model=AccountRecord,
    dependencies=[Depends(require_service_token)],
)
async def lookup_account(
    payload: AccountLookup,
    user_id: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> Account:
    account = await session.scalar(
        select(Account).where(
            Account.user_id == user_id,
            Account.account_number == payload.account_number,
        )
    )
    if account is None and payload.account_number == DEMO_ACCOUNT_NUMBER:
        account = Account(user_id=user_id, account_number=DEMO_ACCOUNT_NUMBER)
        session.add(account)
        await session.commit()
        await session.refresh(account)
        logger.info(
            "demo_account_created",
            "Demo account created for authenticated user",
            outcome=LogOutcome.SUCCESS,
            user_id=user_id,
            attributes={"account_id": account.id},
        )
    if account is None:
        logger.warning(
            "account_not_found",
            "Requested account was not found",
            outcome=LogOutcome.FAILURE,
            user_id=user_id,
            error_type="AccountNotFound",
        )
        raise HTTPException(status_code=404, detail="Account not found")
    logger.info(
        "account_lookup_completed",
        "Account lookup completed",
        outcome=LogOutcome.SUCCESS,
        user_id=user_id,
        attributes={"account_id": account.id},
    )
    return account


@app.post(
    "/internal/v1/accounts/validate",
    response_model=AccountValidationResult,
    dependencies=[Depends(require_service_token)],
)
async def validate_account(
    payload: AccountValidation,
    user_id: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> AccountValidationResult:
    account = await session.get(Account, str(payload.account_id))
    reason: str | None = None
    if account is None or account.user_id != user_id:
        reason = "account_not_found"
    elif not account.is_active:
        reason = "account_inactive"
    elif account.currency != payload.currency:
        reason = "currency_mismatch"
    elif account.balance_minor < payload.amount_minor:
        reason = "insufficient_balance"
    valid = reason is None
    (logger.info if valid else logger.warning)(
        "account_validation_completed",
        "Account validation completed",
        outcome=LogOutcome.SUCCESS if valid else LogOutcome.FAILURE,
        user_id=user_id,
        attributes={"account_id": str(payload.account_id), "reason": reason},
    )
    return AccountValidationResult(account_id=payload.account_id, valid=valid, reason=reason)
