from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from faultweave_common.schemas import (
    TransferCollection,
    TransferCreate,
    TransferEvidenceContract,
    TransferEvidenceExport,
    TransferReview,
    TransferSearchResponse,
)
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TransferWorkflow

CONTRACT_VERSION = "1.0"
SEARCH_FIELDS = {"transfer_id", "request_id", "trace_id", "idempotency_key"}
TRANSFER_STATES = {"RECEIVED", "PROCESSING", "REQUIRES_REVIEW", "COMPLETED"}
SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "password",
    "password_hash",
    "secret",
    "service_token",
    "token",
    "x_service_token",
}


def _combined_history(row: TransferWorkflow, name: str) -> list[dict[str, Any]]:
    transaction_values = list((row.evidence or {}).get(name, []))
    payment = (row.evidence or {}).get("payment") or {}
    payment_values = list((payment.get("evidence") or {}).get(name, []))
    return [*transaction_values, *payment_values]


def _summary(row: TransferWorkflow) -> dict[str, Any]:
    payload = row.payload
    payment = (row.evidence or {}).get("payment") or {}
    payment_evidence = payment.get("evidence") or {}
    events = [*(row.events or []), *(payment.get("events") or [])]
    failures = _combined_history(row, "failure_history")
    reviews = _combined_history(row, "review_history")
    recoveries = _combined_history(row, "recovery_history")
    latest = max((event.get("timestamp") for event in events), default=None)
    return {
        "schema_version": CONTRACT_VERSION,
        "id": row.id,
        "state": row.state,
        "source_account_number": payload["source_account_number"],
        "destination_account_number": payload["destination_account_number"],
        "amount_minor": payload["amount_minor"],
        "currency": payload["currency"],
        "idempotency_key": row.idempotency_key,
        "created_at": row.created_at,
        "latest_event_at": latest,
        "requires_review": row.state == "REQUIRES_REVIEW",
        "funds_status": payment_evidence.get("funds_status", "UNKNOWN"),
        "failure_count": len(failures),
        "review_count": len(reviews),
        "recovery_count": len(recoveries),
    }


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").lower().replace("-", "_")
    if normalized_key in SENSITIVE_KEYS:
        return "[REDACTED]"
    if normalized_key == "reviewed_by":
        return "engineer"
    if isinstance(value, dict):
        return {name: _sanitize(item, key=name) for name, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("bearer "):
        return "[REDACTED]"
    return value


def _evidence_contract(inspection: dict[str, Any]) -> dict[str, Any]:
    payment = inspection.get("payment") or {}
    payment_evidence = payment.get("evidence") or {}
    posting = payment_evidence.get("ledger_posting") or {}
    journal = posting.get("evidence")
    history = inspection.get("case_history") or {}
    return _sanitize(
        {
            "schema_version": CONTRACT_VERSION,
            "transfer_id": inspection["id"],
            "state": inspection["state"],
            "created_at": inspection["created_at"],
            "transfer": inspection["transfer"],
            "funds_status": inspection.get("funds_status", "UNKNOWN"),
            "requires_review": inspection["state"] == "REQUIRES_REVIEW",
            "timeline": inspection.get("timeline", []),
            "case_history": {
                "failures": history.get("failures", []),
                "reviews": history.get("reviews", []),
                "recoveries": history.get("recoveries", []),
            },
            "investigation": inspection.get("investigation") or {},
            "journal": journal,
            "evidence_sources": {
                "workflow": "transactions.transfer_workflows",
                "payment": inspection.get("payment_evidence_source", "unavailable"),
                "journal": "ledger_persisted_posting" if journal else "unavailable",
            },
        }
    )


def _evidence_export(contract: dict[str, Any]) -> dict[str, Any]:
    validated = TransferEvidenceContract.model_validate(contract).model_dump(mode="json")
    canonical = json.dumps(validated, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "schema_version": CONTRACT_VERSION,
        "export_type": "faultweave.transfer-evidence",
        "transfer_id": validated["transfer_id"],
        "evidence": validated,
        "integrity": {
            "algorithm": "SHA-256",
            "canonical_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        },
    }


def _matches(row: TransferWorkflow, field: str, value: str) -> bool:
    if field == "transfer_id":
        return row.id == value
    if field == "idempotency_key":
        return row.idempotency_key == value
    payment = (row.evidence or {}).get("payment") or {}
    events = [*(row.events or []), *(payment.get("events") or [])]
    return any(event.get(field) == value for event in events)


def _history_timestamp(item: dict[str, Any]) -> str:
    return str(item.get("observed_at") or item.get("reviewed_at") or item.get("verified_at") or "")


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
                        "funds_status": payment.get("evidence", {}).get("funds_status", "UNKNOWN"),
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
            owner_authorization = "Bearer " + create_access_token(row.user_id, role="customer")
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
        failures = sorted(
            [
                *result["evidence"].get("failure_history", []),
                *payment_evidence.get("failure_history", []),
            ],
            key=_history_timestamp,
        )
        reviews = sorted(
            [
                *result["evidence"].get("review_history", []),
                *payment_evidence.get("review_history", []),
            ],
            key=_history_timestamp,
        )
        recoveries = sorted(
            [
                *result["evidence"].get("recovery_history", []),
                *payment_evidence.get("recovery_history", []),
            ],
            key=_history_timestamp,
        )
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
            "status": "DEFERRED_TO_GATEWAY_WINDOW",
            "reason": (
                "Use /api/v1/transfers/{transfer_id}/intelligence; the frozen model "
                "requires an independently validated rolling telemetry window"
            ),
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

    async def owned_row(transfer_id, identity, session):
        row = await session.get(TransferWorkflow, transfer_id)
        if row is None or (row.user_id != identity["subject"] and identity["role"] != "engineer"):
            raise HTTPException(status_code=404, detail="Transfer not found")
        return row

    def owner_authorization(row, identity, authorization):
        if identity["role"] == "engineer" and identity["subject"] != row.user_id:
            return "Bearer " + create_access_token(row.user_id, role="customer")
        return authorization

    @router.get("", response_model=TransferCollection)
    async def history(
        identity: dict = Depends(authenticated_identity),
        session: AsyncSession = Depends(get_session),
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        state: str | None = Query(default=None, max_length=40),
    ):
        if state is not None and state not in TRANSFER_STATES:
            raise HTTPException(status_code=422, detail="Unsupported transfer state")
        filters = []
        if identity["role"] != "engineer":
            filters.append(TransferWorkflow.user_id == identity["subject"])
        if state is not None:
            filters.append(TransferWorkflow.state == state)
        query = select(TransferWorkflow)
        count_query = select(func.count()).select_from(TransferWorkflow)
        for condition in filters:
            query = query.where(condition)
            count_query = count_query.where(condition)
        rows = (
            await session.scalars(
                query.order_by(TransferWorkflow.created_at.desc(), TransferWorkflow.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        total = int(await session.scalar(count_query) or 0)
        return {
            "schema_version": CONTRACT_VERSION,
            "scope": "engineer" if identity["role"] == "engineer" else "customer",
            "items": [_summary(row) for row in rows],
            "page": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_more": offset + len(rows) < total,
            },
        }

    @router.get("/search", response_model=TransferSearchResponse)
    async def search(
        field: str = Query(min_length=1, max_length=40),
        value: str = Query(min_length=1, max_length=200),
        identity: dict = Depends(authenticated_identity),
        session: AsyncSession = Depends(get_session),
    ):
        if field not in SEARCH_FIELDS:
            raise HTTPException(
                status_code=422,
                detail="field must be transfer_id, request_id, trace_id or idempotency_key",
            )
        query = select(TransferWorkflow)
        if identity["role"] != "engineer":
            query = query.where(TransferWorkflow.user_id == identity["subject"])
        if field == "transfer_id":
            query = query.where(TransferWorkflow.id == value)
        elif field == "idempotency_key":
            query = query.where(TransferWorkflow.idempotency_key == value)
        rows = (
            await session.scalars(
                query.order_by(TransferWorkflow.created_at.desc(), TransferWorkflow.id.desc())
            )
        ).all()
        matches = [_summary(row) for row in rows if _matches(row, field, value)]
        return {
            "schema_version": CONTRACT_VERSION,
            "query_field": field,
            "exact_match": True,
            "items": matches,
        }

    @router.get("/review-queue", response_model=TransferCollection)
    async def review_queue(
        identity: dict = Depends(authenticated_identity),
        session: AsyncSession = Depends(get_session),
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        if identity["role"] != "engineer":
            raise HTTPException(status_code=403, detail="Engineer role required")
        condition = TransferWorkflow.state == "REQUIRES_REVIEW"
        query = (
            select(TransferWorkflow)
            .where(condition)
            .order_by(TransferWorkflow.created_at.asc(), TransferWorkflow.id.asc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await session.scalars(query)).all()
        total = int(
            await session.scalar(
                select(func.count()).select_from(TransferWorkflow).where(condition)
            )
            or 0
        )
        return {
            "schema_version": CONTRACT_VERSION,
            "scope": "engineer",
            "items": [_summary(row) for row in rows],
            "page": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_more": offset + len(rows) < total,
            },
        }

    @router.get("/{transfer_id}/evidence", response_model=TransferEvidenceContract)
    async def evidence_contract(
        transfer_id: str,
        authorization: str = Header(...),
        identity: dict = Depends(authenticated_identity),
        session: AsyncSession = Depends(get_session),
    ):
        row = await owned_row(transfer_id, identity, session)
        inspection = await inspect_row(row, owner_authorization(row, identity, authorization))
        return _evidence_contract(inspection)

    @router.get("/{transfer_id}/evidence/export", response_model=TransferEvidenceExport)
    async def export_evidence(
        transfer_id: str,
        authorization: str = Header(...),
        identity: dict = Depends(authenticated_identity),
        session: AsyncSession = Depends(get_session),
    ):
        row = await owned_row(transfer_id, identity, session)
        inspection = await inspect_row(row, owner_authorization(row, identity, authorization))
        return _evidence_export(_evidence_contract(inspection))

    @router.get("/{transfer_id}")
    async def inspect(
        transfer_id: str,
        authorization: str = Header(...),
        identity: dict = Depends(authenticated_identity),
        session: AsyncSession = Depends(get_session),
    ):
        row = await owned_row(transfer_id, identity, session)
        return await inspect_row(row, owner_authorization(row, identity, authorization))

    return router
