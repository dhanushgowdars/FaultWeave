from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from faultweave_common.schemas import TransferCreate, TransferReview
from faultweave_common.security import authenticated_subject, require_service_token
from faultweave_common.transfer_http import call_service, failure_detail
from faultweave_common.transfer_workflow import (
    operation_lock,
    record_event,
    snapshot,
    workflow_id,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TransferWorkflow


def build_router(factory, get_session):
    router = APIRouter(
        prefix="/internal/v1/transfers", dependencies=[Depends(require_service_token)]
    )

    async def execute(row, session, authorization, review=None):
        row.state = "PROCESSING"
        row.last_error = None
        await record_event(
            session,
            row,
            "transaction",
            "payment_execution",
            "STARTED",
            "Requesting payment processing with a stable transfer identity",
        )
        try:
            payment = await call_service(
                "transaction",
                "payment",
                "/internal/v1/transfer-payments",
                authorization,
                {"transfer": row.payload, "review": review.model_dump() if review else None},
            )
            row.evidence = {**row.evidence, "payment": payment}
            if payment["state"] != "COMPLETED":
                row.state = "REQUIRES_REVIEW"
                row.last_error = payment.get("last_error") or {
                    "step": "payment_execution",
                    "detail": "Payment outcome requires review",
                }
                await record_event(
                    session,
                    row,
                    "transaction",
                    "payment_execution",
                    "FAILED",
                    "Transfer requires engineer review; inspect the payment evidence",
                )
                return snapshot(row)
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
            row.state = "REQUIRES_REVIEW"
            row.last_error = failure_detail(exc, "payment_execution")
            await record_event(
                session,
                row,
                "transaction",
                "payment_execution",
                "FAILED",
                "Payment confirmation unavailable; transfer outcome requires review",
            )
            return snapshot(row)
        row.state = "COMPLETED"
        await record_event(
            session,
            row,
            "transaction",
            "transfer_completed",
            "SUCCEEDED",
            "Payment, committed balances, and ledger posting confirmed",
        )
        return snapshot(row)

    @router.post("")
    async def start(
        payload: TransferCreate,
        authorization: str = Header(...),
        subject: str = Depends(authenticated_subject),
        session: AsyncSession = Depends(get_session),
    ):
        key = payload.idempotency_key
        async with operation_lock(factory, "transaction", subject, key):
            row = await session.get(TransferWorkflow, workflow_id("transaction", subject, key))
            if row is not None:
                if row.payload != payload.model_dump():
                    raise HTTPException(status_code=409, detail="Idempotency key payload mismatch")
                # Replaying POST only reads the prior outcome. Retry requires a review note.
                return snapshot(row)
            row = TransferWorkflow(
                id=workflow_id("transaction", subject, key),
                user_id=subject,
                idempotency_key=key,
                payload=payload.model_dump(),
                events=[],
                evidence={},
                state="RECEIVED",
            )
            session.add(row)
            await record_event(
                session,
                row,
                "transaction",
                "transfer_received",
                "SUCCEEDED",
                "Authenticated transfer instruction durably received",
            )
            return await execute(row, session, authorization)

    @router.post("/{transfer_id}/resume")
    async def resume(
        transfer_id: str,
        review: TransferReview,
        authorization: str = Header(...),
        subject: str = Depends(authenticated_subject),
        session: AsyncSession = Depends(get_session),
    ):
        row = await session.get(TransferWorkflow, transfer_id)
        if row is None or row.user_id != subject:
            raise HTTPException(status_code=404, detail="Transfer not found")
        async with operation_lock(factory, "transaction", subject, row.idempotency_key):
            await session.refresh(row)
            if row.state == "COMPLETED":
                return snapshot(row)
            # RECEIVED/PROCESSING can remain after a crash; acquiring the lock proves
            # there is no active transaction-service operation for this identity.
            await record_event(
                session, row, "transaction", "engineer_review", "REVIEWED", review.note
            )
            return await execute(row, session, authorization, review)

    async def inspect_row(row, authorization):
        result = snapshot(row)
        payment_id = workflow_id("payment", row.user_id, row.idempotency_key)
        try:
            payment = await call_service(
                "transaction",
                "payment",
                f"/internal/v1/transfer-payments/{payment_id}",
                authorization,
                method="GET",
            )
            result["payment_evidence_source"] = "live_database"
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            payment = row.evidence.get("payment")
            result["payment_evidence_source"] = "last_saved" if payment else "unavailable"
            result["inspection_error"] = failure_detail(exc, "payment_evidence")
        result["payment"] = payment
        result["funds_status"] = (
            payment["evidence"].get("funds_status", "UNKNOWN") if payment else "UNKNOWN"
        )
        result["timeline"] = sorted(
            [*row.events, *((payment or {}).get("events", []))],
            key=lambda event: (event["timestamp"], event["service"], event["sequence"]),
        )
        error = row.last_error or {}
        failed_events = [event for event in result["timeline"] if event["status"] == "FAILED"]
        affected_service = {
            "payment_execution": "payment",
            "account_commit": "account",
            "ledger_posting": "ledger",
        }.get(error.get("step"))
        result["investigation"] = {
            "requires_review": row.state == "REQUIRES_REVIEW",
            "observed_failure_service": affected_service,
            "observed_error": error or None,
            "cause_status": "not_confirmed" if error else "no_current_failure",
            "evidence_event_ids": [event["event_id"] for event in failed_events],
            "next_action": (
                "Inspect the failed service and its logs, restore the dependency, "
                "then submit a review note to resume this same transfer."
                if row.state == "REQUIRES_REVIEW"
                else "Inspect the saved case evidence."
            ),
            "funds_explanation": {
                "COMMITTED": "Debit and credit confirmed; reuse this transfer for retries.",
                "NOT_COMMITTED": "Account request rejected before funds movement.",
                "NOT_ATTEMPTED": "Payment has not requested account funds movement.",
                "UNKNOWN": "Outcome unconfirmed; reconcile with the original transfer key.",
            }[result["funds_status"]],
        }
        result["model_prediction"] = {
            "status": "NOT_EVALUATED",
            "reason": "This transfer case has no validated model feature window yet",
        }
        return result

    @router.get("/by-key/{key}")
    async def inspect_key(
        key: str,
        authorization: str = Header(...),
        subject: str = Depends(authenticated_subject),
        session: AsyncSession = Depends(get_session),
    ):
        row = await session.get(TransferWorkflow, workflow_id("transaction", subject, key))
        if row is None:
            raise HTTPException(status_code=404, detail="Transfer not found")
        return await inspect_row(row, authorization)

    @router.get("/{transfer_id}")
    async def inspect(
        transfer_id: str,
        authorization: str = Header(...),
        subject: str = Depends(authenticated_subject),
        session: AsyncSession = Depends(get_session),
    ):
        row = await session.get(TransferWorkflow, transfer_id)
        if row is None or row.user_id != subject:
            raise HTTPException(status_code=404, detail="Transfer not found")
        return await inspect_row(row, authorization)

    return router
