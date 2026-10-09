from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from faultweave_common.schemas import TransferCreate, TransferReview
from faultweave_common.security import (
    authenticated_identity,
    authenticated_subject,
    create_access_token,
    require_service_token,
)
from faultweave_common.transfer_http import call_service, failure_detail
from faultweave_common.transfer_workflow import (
    append_evidence,
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

    async def execute(row, session, authorization, review=None, reviewed_by=None):
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
                {
                    "transfer": row.payload,
                    "review": review.model_dump() if review else None,
                    "reviewed_by": reviewed_by,
                },
            )
            row.evidence = {**row.evidence, "payment": payment}
            if payment["state"] != "COMPLETED":
                row.state = "REQUIRES_REVIEW"
                row.last_error = payment.get("last_error") or {
                    "step": "payment_execution",
                    "detail": "Payment outcome requires review",
                }
                event = await record_event(
                    session,
                    row,
                    "transaction",
                    "payment_execution",
                    "FAILED",
                    "Transfer requires engineer review; inspect the payment evidence",
                )
                await append_evidence(
                    session,
                    row,
                    "failure_history",
                    {
                        "observed_at": event["timestamp"],
                        "event_id": event["event_id"],
                        "attempt": event["attempt"],
                        "service": "payment",
                        "step": row.last_error.get("step", "payment_execution"),
                        "error": row.last_error,
                        "funds_status": payment.get("evidence", {}).get(
                            "funds_status", "UNKNOWN"
                        ),
                    },
                )
                return snapshot(row)
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
            row.state = "REQUIRES_REVIEW"
            row.last_error = failure_detail(exc, "payment_execution")
            event = await record_event(
                session,
                row,
                "transaction",
                "payment_execution",
                "FAILED",
                "Payment confirmation unavailable; transfer outcome requires review",
            )
            await append_evidence(
                session,
                row,
                "failure_history",
                {
                    "observed_at": event["timestamp"],
                    "event_id": event["event_id"],
                    "attempt": event["attempt"],
                    "service": "payment",
                    "step": "payment_execution",
                    "error": row.last_error,
                    "funds_status": "UNKNOWN",
                },
            )
            return snapshot(row)
        row.state = "COMPLETED"
        event = await record_event(
            session,
            row,
            "transaction",
            "transfer_completed",
            "SUCCEEDED",
            "Payment, committed balances, and ledger posting confirmed",
        )
        if row.evidence.get("failure_history"):
            await append_evidence(
                session,
                row,
                "recovery_history",
                {
                    "verified_at": event["timestamp"],
                    "event_id": event["event_id"],
                    "attempt": event["attempt"],
                    "status": "VERIFIED_BY_COMPLETION",
                    "detail": "The same transfer identity completed after reviewed recovery",
                },
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
        identity: dict = Depends(authenticated_identity),
        session: AsyncSession = Depends(get_session),
    ):
        if identity["role"] != "engineer":
            raise HTTPException(status_code=403, detail="Engineer role required")
        reviewer = identity["subject"]
        row = await session.get(TransferWorkflow, transfer_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Transfer not found")
        async with operation_lock(factory, "transaction", row.user_id, row.idempotency_key):
            await session.refresh(row)
            if row.state == "COMPLETED":
                return snapshot(row)
            # RECEIVED/PROCESSING can remain after a crash; acquiring the lock proves
            # there is no active transaction-service operation for this identity.
            event = await record_event(
                session, row, "transaction", "engineer_review", "REVIEWED", review.note
            )
            await append_evidence(
                session,
                row,
                "review_history",
                {
                    "reviewed_at": event["timestamp"],
                    "event_id": event["event_id"],
                    "attempt": event["attempt"],
                    "reviewed_by": reviewer,
                    "decision": "RESUME_SAME_TRANSFER",
                    "note": review.note,
                },
            )
            owner_authorization = (
                "Bearer " + create_access_token(row.user_id, role="customer")
            )
            return await execute(
                row,
                session,
                owner_authorization,
                review,
                reviewed_by=reviewer,
            )

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
        payment_evidence = (payment or {}).get("evidence", {})
        failures = [
            *result["evidence"].get("failure_history", []),
            *payment_evidence.get("failure_history", []),
        ]
        reviews = [
            *result["evidence"].get("review_history", []),
            *payment_evidence.get("review_history", []),
        ]
        recoveries = [
            *result["evidence"].get("recovery_history", []),
            *payment_evidence.get("recovery_history", []),
        ]
        failed_events = [event for event in result["timeline"] if event["status"] == "FAILED"]
        affected_service = {
            "payment_execution": "payment",
            "account_commit": "account",
            "ledger_posting": "ledger",
        }.get(error.get("step"))
        latest_failure = failures[-1] if failures else None
        result["case_history"] = {
            "failures": failures,
            "reviews": reviews,
            "recoveries": recoveries,
        }
        result["investigation"] = {
            "requires_review": row.state == "REQUIRES_REVIEW",
            "observed_failure_service": (
                latest_failure.get("service") if latest_failure else affected_service
            ),
            "observed_error": error or (latest_failure or {}).get("error"),
            "cause_status": (
                "recovered_after_review"
                if row.state == "COMPLETED" and failures
                else "observed_failure_root_cause_unconfirmed"
                if failures or error
                else "no_failure_observed"
            ),
            "evidence_event_ids": [event["event_id"] for event in failed_events],
            "next_action": (
                "Inspect the failed service and its logs, restore the dependency, "
                "then submit a review note to resume this same transfer."
                if row.state == "REQUIRES_REVIEW"
                else "Recovery is confirmed; retain the failure, review, and recovery evidence."
                if failures
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
        identity: dict = Depends(authenticated_identity),
        session: AsyncSession = Depends(get_session),
    ):
        row = await session.get(TransferWorkflow, transfer_id)
        if row is None or (
            row.user_id != identity["subject"] and identity["role"] != "engineer"
        ):
            raise HTTPException(status_code=404, detail="Transfer not found")
        owner_authorization = authorization
        if identity["role"] == "engineer" and identity["subject"] != row.user_id:
            owner_authorization = (
                "Bearer " + create_access_token(row.user_id, role="customer")
            )
        return await inspect_row(row, owner_authorization)

    return router
