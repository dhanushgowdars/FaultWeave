from __future__ import annotations

import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from faultweave_common.db import (
    database_ready,
    ensure_schema,
    make_engine,
    make_session_factory,
)
from faultweave_common.fault_injection import (
    apply_connection_pool_exhaustion,
    apply_database_latency,
)
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import CorrelationMiddleware
from faultweave_common.schemas import (
    AccountLookup,
    AccountRecord,
    AccountValidation,
    AccountValidationResult,
    DemoAccountCreate,
    HealthResponse,
    TransferCreate,
    TransferEvidence,
    TransferJournalEntry,
    TransferRecord,
)
from faultweave_common.security import authenticated_subject, require_service_token
from faultweave_common.transfer_workflow import operation_lock
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Account, Base, Transfer, TransferEntry

engine = make_engine()
session_factory = make_session_factory(engine)
logger = configure_logging("account")
DEMO_ACCOUNT_NUMBER = os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001")


async def get_session():
    await apply_connection_pool_exhaustion(engine, "account")
    await apply_database_latency("account")
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
        existing = await session.scalar(
            select(Account).where(Account.account_number == DEMO_ACCOUNT_NUMBER)
        )
        if existing is None:
            account = Account(user_id=user_id, account_number=DEMO_ACCOUNT_NUMBER)
            session.add(account)
            try:
                await session.commit()
            except IntegrityError:
                # Another request may have created the globally unique demo account.
                # Re-query by owner so that a different user's account is never exposed.
                await session.rollback()
                account = await session.scalar(
                    select(Account).where(
                        Account.user_id == user_id,
                        Account.account_number == DEMO_ACCOUNT_NUMBER,
                    )
                )
            else:
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


@app.post(
    "/internal/v1/accounts/demo/create",
    response_model=AccountRecord,
    dependencies=[Depends(require_service_token)],
)
async def create_demo_account(
    payload: DemoAccountCreate,
    user_id: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> Account:
    # Demo subaccounts start empty; only the original seeded account has an initial balance.
    if payload.account_number == DEMO_ACCOUNT_NUMBER:
        raise HTTPException(status_code=409, detail="Use account lookup for the seeded account")
    async with session.begin():
        account = await session.scalar(
            select(Account).where(Account.account_number == payload.account_number)
        )
        if account is not None:
            raise HTTPException(status_code=409, detail="Account number already exists")
        account = Account(
            user_id=user_id,
            account_number=payload.account_number,
            balance_minor=0,
        )
        session.add(account)
        await session.flush()
    logger.info(
        "demo_subaccount_created",
        "Empty demo subaccount created",
        outcome=LogOutcome.SUCCESS,
        user_id=user_id,
        attributes={"account_id": account.id},
    )
    return account


def transfer_rejection(
    source: Account, destination: Account, payload: TransferCreate
) -> str | None:
    if source.id == destination.id:
        return "source_and_destination_must_differ"
    if not source.is_active or not destination.is_active:
        return "account_inactive"
    if source.currency != payload.currency or destination.currency != payload.currency:
        return "currency_mismatch"
    if source.balance_minor < payload.amount_minor:
        return "insufficient_balance"
    return None


@app.post(
    "/internal/v1/accounts/transfers",
    response_model=TransferRecord,
    dependencies=[Depends(require_service_token)],
)
async def transfer_funds(
    payload: TransferCreate,
    user_id: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> Transfer:
    async with operation_lock(session_factory, "account", user_id, payload.idempotency_key):
        return await commit_transfer(payload, user_id, session)


async def commit_transfer(payload: TransferCreate, user_id: str, session: AsyncSession) -> Transfer:
    # Both balances and journal entries share one database commit. Row locks serialize
    # competing transfers and idempotent retries for the same source account.
    async with session.begin():
        candidates = (
            await session.scalars(
                select(Account)
                .where(
                    Account.account_number.in_(
                        [payload.source_account_number, payload.destination_account_number]
                    )
                )
                .order_by(Account.id)
                .with_for_update()
            )
        ).all()
        by_number = {account.account_number: account for account in candidates}
        source = by_number.get(payload.source_account_number)
        destination = by_number.get(payload.destination_account_number)
        if source is None or destination is None or source.user_id != user_id:
            raise HTTPException(status_code=404, detail="Account not found")
        prior = await session.scalar(
            select(Transfer).where(
                Transfer.user_id == user_id,
                Transfer.idempotency_key == payload.idempotency_key,
            )
        )
        if prior is not None:
            if (
                prior.source_account_id != source.id
                or prior.destination_account_id != destination.id
                or prior.amount_minor != payload.amount_minor
                or prior.currency != payload.currency
            ):
                raise HTTPException(
                    status_code=409, detail="Idempotency key reused with different transfer"
                )
            return prior
        reason = transfer_rejection(source, destination, payload)
        if reason:
            logger.warning(
                "transfer_rejected",
                "Demo transfer rejected before funds movement",
                outcome=LogOutcome.FAILURE,
                error_type=reason,
                attributes={"source_account_id": source.id, "reason": reason},
            )
            raise HTTPException(status_code=409, detail=reason)
        transfer_id = str(uuid4())
        debit_id, credit_id = str(uuid4()), str(uuid4())
        source.balance_minor -= payload.amount_minor
        destination.balance_minor += payload.amount_minor
        record = Transfer(
            id=transfer_id,
            user_id=user_id,
            source_account_id=source.id,
            destination_account_id=destination.id,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            idempotency_key=payload.idempotency_key,
            status="COMPLETED",
            debit_entry_id=debit_id,
            credit_entry_id=credit_id,
        )
        session.add(record)
        await session.flush()
        session.add_all(
            [
                TransferEntry(
                    id=debit_id,
                    transfer_id=transfer_id,
                    account_id=source.id,
                    direction="DEBIT",
                    amount_minor=payload.amount_minor,
                    currency=payload.currency,
                ),
                TransferEntry(
                    id=credit_id,
                    transfer_id=transfer_id,
                    account_id=destination.id,
                    direction="CREDIT",
                    amount_minor=payload.amount_minor,
                    currency=payload.currency,
                ),
            ]
        )
        await session.flush()
    logger.info(
        "demo_transfer_completed",
        "Demo funds moved and paired entries committed",
        outcome=LogOutcome.SUCCESS,
        transaction_id=transfer_id,
        attributes={"source_account_id": source.id, "destination_account_id": destination.id},
    )
    return record


@app.get(
    "/internal/v1/accounts/transfers/{transfer_id}",
    response_model=TransferEvidence,
    dependencies=[Depends(require_service_token)],
)
async def get_transfer(
    transfer_id: str,
    user_id: str = Depends(authenticated_subject),
    session: AsyncSession = Depends(get_session),
) -> TransferEvidence:
    record = await session.get(Transfer, transfer_id)
    if record is None or record.user_id != user_id:
        raise HTTPException(status_code=404, detail="Transfer not found")
    entries = (
        await session.scalars(
            select(TransferEntry)
            .where(TransferEntry.transfer_id == transfer_id)
            .order_by(TransferEntry.direction.desc())
        )
    ).all()
    return TransferEvidence(
        transfer=TransferRecord.model_validate(record),
        entries=[TransferJournalEntry.model_validate(entry) for entry in entries],
    )
