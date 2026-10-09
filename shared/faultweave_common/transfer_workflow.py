"""Durable demo workflow records and PostgreSQL transaction-scoped operation locks."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import JSON, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .logging import (
    LogOutcome,
    configure_logging,
    request_id_context,
    run_id_context,
    trace_id_context,
)


class TransferWorkflowMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(40), default="RECEIVED")
    events: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


def workflow_id(service: str, subject: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"faultweave:{service}:{subject}:{key}"))


@asynccontextmanager
async def operation_lock(factory, service: str, subject: str, key: str):
    lock_name = f"faultweave:{service}:{subject}:{key}"
    lock_key = int.from_bytes(hashlib.sha256(lock_name.encode()).digest()[:8], signed=True)
    # Separate connection: workflow events may commit while this lock remains held.
    # PostgreSQL releases a transaction-scoped lock on disconnect/rollback as well.
    async with factory() as session, session.begin():
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}
        )
        if not acquired:
            raise HTTPException(status_code=409, detail="Operation is already running")
        yield


async def record_event(session, row, service: str, step: str, status: str, detail: str):
    event = {
        "event_id": str(uuid4()),
        "sequence": len(row.events or []) + 1,
        "attempt": 1
        + sum(item["step"] in {"review", "engineer_review"} for item in (row.events or []))
        + int(step in {"review", "engineer_review"}),
        "timestamp": datetime.now(UTC).isoformat(),
        "service": service,
        "step": step,
        "status": status,
        "detail": detail,
        "request_id": request_id_context.get(),
        "trace_id": trace_id_context.get(),
        "run_id": run_id_context.get(),
    }
    row.events = [*(row.events or []), event]
    await session.commit()
    outcome = {"SUCCEEDED": LogOutcome.SUCCESS, "FAILED": LogOutcome.FAILURE}.get(
        status, LogOutcome.UNKNOWN
    )
    log = configure_logging(service)
    emit = log.warning if status == "FAILED" else log.info
    emit(
        "transfer_workflow_step",
        detail,
        outcome=outcome,
        transaction_id=row.id,
        attributes=event,
    )


def snapshot(row) -> dict:
    return {
        "id": row.id,
        "state": row.state,
        "transfer": row.payload,
        "events": row.events,
        "evidence": row.evidence,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat(),
    }
