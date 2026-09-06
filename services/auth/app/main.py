from __future__ import annotations

import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from faultweave_common.db import database_ready, make_engine, make_session_factory
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import RequestIdMiddleware
from faultweave_common.schemas import HealthResponse, LoginRequest, LoginResponse
from faultweave_common.security import create_access_token, hash_password, verify_password
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Base, User

engine = make_engine()
session_factory = make_session_factory(engine)
logger = configure_logging("authentication")


async def get_session():
    async with session_factory() as session:
        yield session


async def seed_demo_user() -> None:
    username = os.getenv("DEMO_USERNAME", "demo")
    password = os.getenv("DEMO_PASSWORD", "faultweave-demo")
    async with session_factory() as session:
        statement = (
            insert(User)
            .values(username=username, password_hash=hash_password(password))
            .on_conflict_do_nothing(index_elements=[User.username])
        )
        await session.execute(statement)
        await session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await seed_demo_user()
    yield
    await engine.dispose()


app = FastAPI(title="FaultWeave Authentication Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware, service="authentication")


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="authentication", status="up")


@app.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    ready_now = await database_ready(engine)
    if not ready_now:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(service="authentication", status="up", database="up")


@app.post("/internal/v1/auth/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    user = await session.scalar(select(User).where(User.username == payload.username))
    credentials_invalid = (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    )
    if credentials_invalid:
        logger.warning(
            "authentication_failed",
            "Authentication attempt was rejected",
            outcome=LogOutcome.FAILURE,
            error_type="InvalidCredentials",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    logger.info(
        "authentication_succeeded",
        "User authentication succeeded",
        outcome=LogOutcome.SUCCESS,
        user_id=user.id,
    )
    return LoginResponse(access_token=create_access_token(user.id), user_id=UUID(user.id))
