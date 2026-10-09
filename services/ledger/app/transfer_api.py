from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.schemas import TransferEvidence, TransferPostingRequest
from faultweave_common.security import authenticated_subject, require_service_token
from faultweave_common.transfer_http import call_service
from faultweave_common.transfer_workflow import operation_lock
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TransferPosting


def validate_journal(evidence: TransferEvidence):
    transfer = evidence.transfer
    entries = {entry.direction: entry for entry in evidence.entries}
    if len(evidence.entries) != 2 or set(entries) != {"DEBIT", "CREDIT"}:
        raise HTTPException(status_code=409, detail="Account journal is not a debit-credit pair")
    if transfer.status != "COMPLETED":
        raise HTTPException(status_code=409, detail="Account transfer is not committed")
    for direction, account_id, entry_id in (
        ("DEBIT", transfer.source_account_id, transfer.debit_entry_id),
        ("CREDIT", transfer.destination_account_id, transfer.credit_entry_id),
    ):
        entry = entries[direction]
        if (
            entry.transfer_id != transfer.id
            or entry.account_id != account_id
            or entry.id != entry_id
            or entry.amount_minor != transfer.amount_minor
            or entry.currency != transfer.currency
        ):
            raise HTTPException(status_code=409, detail="Account journal does not match transfer")


def build_router(factory, get_session):
    router = APIRouter(
        prefix="/internal/v1/transfer-postings", dependencies=[Depends(require_service_token)]
    )

    @router.post("")
    async def post_transfer(
        payload: TransferPostingRequest,
        authorization: str = Header(...),
        subject: str = Depends(authenticated_subject),
        session: AsyncSession = Depends(get_session),
    ):
        key = str(payload.account_transfer_id)
        async with operation_lock(factory, "ledger", subject, key):
            prior = await session.get(TransferPosting, key)
            if prior:
                if prior.user_id != subject:
                    raise HTTPException(status_code=404, detail="Posting not found")
                return {"id": prior.id, "evidence": prior.evidence, "recorded_at": prior.created_at}
            result = await call_service(
                "ledger",
                "account",
                f"/internal/v1/accounts/transfers/{key}",
                authorization,
                method="GET",
            )
            evidence = TransferEvidence.model_validate(result)
            validate_journal(evidence)
            row = TransferPosting(
                id=key, user_id=subject, evidence=evidence.model_dump(mode="json")
            )
            session.add(row)
            await session.commit()
            configure_logging("ledger").info(
                "transfer_ledger_posted",
                "Balanced transfer journal verified and recorded",
                outcome=LogOutcome.SUCCESS,
                transaction_id=key,
                attributes={
                    "debit_entry_id": str(evidence.transfer.debit_entry_id),
                    "credit_entry_id": str(evidence.transfer.credit_entry_id),
                },
            )
            return {"id": row.id, "evidence": row.evidence, "recorded_at": row.created_at}

    return router
