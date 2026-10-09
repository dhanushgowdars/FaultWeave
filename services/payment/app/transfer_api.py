from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from faultweave_common.schemas import (
    PaymentTransferExecution,
    TransferPostingRecord,
    TransferRecord,
)
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
        prefix="/internal/v1/transfer-payments", dependencies=[Depends(require_service_token)]
    )

    @router.get("/{payment_id}")
    async def inspect_payment(
        payment_id: str,
        subject: str = Depends(authenticated_subject),
        session: AsyncSession = Depends(get_session),
    ):
        row = await session.get(TransferWorkflow, payment_id)
        if row is None or row.user_id != subject:
            raise HTTPException(status_code=404, detail="Transfer payment not found")
        return snapshot(row)

    @router.post("")
    async def execute(
        payload: PaymentTransferExecution,
        authorization: str = Header(...),
        subject: str = Depends(authenticated_subject),
        session: AsyncSession = Depends(get_session),
    ):
        transfer = payload.transfer
        key = transfer.idempotency_key
        async with operation_lock(factory, "payment", subject, key):
            row = await session.get(TransferWorkflow, workflow_id("payment", subject, key))
            if row is not None:
                if row.payload != transfer.model_dump():
                    raise HTTPException(status_code=409, detail="Idempotency key payload mismatch")
                if row.state == "COMPLETED" or payload.review is None:
                    return snapshot(row)
                await record_event(
                    session, row, "payment", "review", "REVIEWED", payload.review.note
                )
            else:
                row = TransferWorkflow(
                    id=workflow_id("payment", subject, key),
                    user_id=subject,
                    idempotency_key=key,
                    payload=transfer.model_dump(),
                    events=[],
                    evidence={"funds_status": "NOT_ATTEMPTED"},
                    state="RECEIVED",
                )
                session.add(row)
                await record_event(
                    session,
                    row,
                    "payment",
                    "payment_received",
                    "SUCCEEDED",
                    "Payment instruction durably received",
                )
            row.state = "PROCESSING"
            row.last_error = None
            step = "account_commit"
            try:
                # A timeout could occur after a commit: UNKNOWN is intentional until confirmed.
                if row.evidence.get("funds_status") != "COMMITTED":
                    row.evidence = {**row.evidence, "funds_status": "UNKNOWN"}
                await record_event(
                    session,
                    row,
                    "payment",
                    step,
                    "STARTED",
                    "Requesting atomic debit and credit with the original idempotency key",
                )
                result = await call_service(
                    "payment",
                    "account",
                    "/internal/v1/accounts/transfers",
                    authorization,
                    transfer.model_dump(),
                )
                committed = TransferRecord.model_validate(result)
                row.evidence = {
                    **row.evidence,
                    "funds_status": "COMMITTED",
                    "account_transfer": committed.model_dump(mode="json"),
                }
                await record_event(
                    session,
                    row,
                    "payment",
                    step,
                    "SUCCEEDED",
                    "Account service confirmed committed debit and credit",
                )
                step = "ledger_posting"
                await record_event(
                    session,
                    row,
                    "payment",
                    step,
                    "STARTED",
                    "Requesting ledger verification of the committed account journal",
                )
                posting = await call_service(
                    "payment",
                    "ledger",
                    "/internal/v1/transfer-postings",
                    authorization,
                    {"account_transfer_id": str(committed.id)},
                )
                validated_posting = TransferPostingRecord.model_validate(posting)
                if (
                    validated_posting.id != committed.id
                    or validated_posting.evidence.transfer.model_dump(exclude={"created_at"})
                    != committed.model_dump(exclude={"created_at"})
                ):
                    raise ValueError("Ledger confirmation does not match the account transfer")
                row.evidence = {
                    **row.evidence,
                    "ledger_posting": validated_posting.model_dump(mode="json"),
                }
                await record_event(
                    session,
                    row,
                    "payment",
                    step,
                    "SUCCEEDED",
                    "Ledger service confirmed the persisted balanced posting",
                )
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
                row.last_error = failure_detail(exc, step)
                row.state = "REQUIRES_REVIEW"
                if (
                    step == "account_commit"
                    and isinstance(exc, httpx.HTTPStatusError)
                    and (
                        exc.response.status_code in {400, 401, 403, 404, 422}
                        or (
                            exc.response.status_code == 409
                            and row.last_error["detail"]
                            in {
                                "insufficient_balance",
                                "account_inactive",
                                "currency_mismatch",
                                "source_and_destination_must_differ",
                            }
                        )
                    )
                    and row.evidence.get("funds_status") != "COMMITTED"
                ):
                    row.evidence = {**row.evidence, "funds_status": "NOT_COMMITTED"}
                await record_event(
                    session,
                    row,
                    "payment",
                    step,
                    "FAILED",
                    "Payment paused for review: " + row.last_error["detail"],
                )
                return snapshot(row)
            row.state = "COMPLETED"
            await record_event(
                session,
                row,
                "payment",
                "payment_completed",
                "SUCCEEDED",
                "Funds movement and ledger posting confirmed",
            )
            return snapshot(row)

    return router
