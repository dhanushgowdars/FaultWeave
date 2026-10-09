"""Authenticated entry points for durable transfer workflows and case evidence."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from faultweave_common.schemas import (
    AccountRecord,
    DemoAccountCreate,
    LoginRequest,
    LoginResponse,
    TransferCreate,
    TransferReview,
)
from faultweave_common.security import authenticated_subject
from faultweave_common.transfer_http import call_service

from .intelligence_artifacts import get_frozen_artifacts
from .intelligence_live import get_live_telemetry_buffer
from .transfer_intelligence import build_transfer_intelligence

router = APIRouter(prefix="/api/v1")


async def _proxy(target, path, request, payload=None, method="POST"):
    try:
        return await call_service(
            "gateway", target, path, request.headers.get("authorization"), payload, method
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "Operation rejected")
        except ValueError:
            detail = "Downstream operation failed"
        status = exc.response.status_code
        raise HTTPException(status_code=status if status < 500 else 502, detail=detail) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail="Response unavailable. Inspect the original idempotency key before retrying.",
        ) from exc


@router.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request):
    return await _proxy("authentication", "/internal/v1/auth/login", request, payload.model_dump())


@router.get("/accounts/{account_number}", response_model=AccountRecord)
async def account(account_number: str, request: Request, _: str = Depends(authenticated_subject)):
    return await _proxy(
        "account", "/internal/v1/accounts/lookup", request, {"account_number": account_number}
    )


@router.post("/accounts", response_model=AccountRecord)
async def create_account(
    payload: DemoAccountCreate, request: Request, _: str = Depends(authenticated_subject)
):
    return await _proxy(
        "account", "/internal/v1/accounts/demo/create", request, payload.model_dump()
    )


@router.post("/transfers")
async def transfer(
    payload: TransferCreate, request: Request, _: str = Depends(authenticated_subject)
):
    return await _proxy("transaction", "/internal/v1/transfers", request, payload.model_dump())


@router.get("/transfers/by-key/{key}")
async def transfer_by_key(key: str, request: Request, _: str = Depends(authenticated_subject)):
    return await _proxy(
        "transaction", f"/internal/v1/transfers/by-key/{key}", request, method="GET"
    )


@router.get("/transfers/{transfer_id}")
async def evidence(transfer_id: str, request: Request, _: str = Depends(authenticated_subject)):
    return await _proxy(
        "transaction", f"/internal/v1/transfers/{transfer_id}", request, method="GET"
    )


@router.get("/transfers/{transfer_id}/intelligence")
async def transfer_intelligence(
    transfer_id: str,
    request: Request,
    _: str = Depends(authenticated_subject),
):
    transfer_evidence = await _proxy(
        "transaction", f"/internal/v1/transfers/{transfer_id}", request, method="GET"
    )
    return build_transfer_intelligence(
        transfer_evidence,
        get_live_telemetry_buffer(),
        get_frozen_artifacts(),
    )


@router.post("/transfers/{transfer_id}/resume")
async def reviewed_retry(
    transfer_id: str,
    review: TransferReview,
    request: Request,
    _: str = Depends(authenticated_subject),
):
    return await _proxy(
        "transaction", f"/internal/v1/transfers/{transfer_id}/resume", request, review.model_dump()
    )
