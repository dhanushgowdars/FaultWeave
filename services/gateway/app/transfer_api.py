"""Authenticated entry points for durable transfer workflows and case evidence."""

from __future__ import annotations

import hashlib
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from faultweave_common.schemas import (
    AccountRecord,
    DemoAccountCreate,
    IncidentAssistantRequest,
    IncidentAssistantResponse,
    LoginRequest,
    LoginResponse,
    TransferCollection,
    TransferCreate,
    TransferEvidenceContract,
    TransferEvidenceExport,
    TransferReview,
    TransferSearchResponse,
)
from faultweave_common.security import authenticated_identity, authenticated_subject
from faultweave_common.transfer_http import call_service

from .incident_assistant import GroundedIncidentAssistant, get_incident_assistant
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


@router.get("/transfers", response_model=TransferCollection)
async def transfer_history(
    request: Request,
    _: str = Depends(authenticated_subject),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    state: str | None = Query(default=None, max_length=40),
):
    query = urlencode(
        {
            key: value
            for key, value in {"limit": limit, "offset": offset, "state": state}.items()
            if value is not None
        }
    )
    return await _proxy("transaction", f"/internal/v1/transfers?{query}", request, method="GET")


@router.get("/transfers/search", response_model=TransferSearchResponse)
async def search_transfers(
    request: Request,
    field: str = Query(min_length=1, max_length=40),
    value: str = Query(min_length=1, max_length=200),
    _: str = Depends(authenticated_subject),
):
    query = urlencode({"field": field, "value": value})
    return await _proxy(
        "transaction", f"/internal/v1/transfers/search?{query}", request, method="GET"
    )


@router.get("/engineer/review-queue", response_model=TransferCollection)
async def transfer_review_queue(
    request: Request,
    _: str = Depends(authenticated_subject),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = urlencode({"limit": limit, "offset": offset})
    return await _proxy(
        "transaction", f"/internal/v1/transfers/review-queue?{query}", request, method="GET"
    )


@router.get("/contracts/transfer-intelligence")
async def transfer_contract(
    _: str = Depends(authenticated_subject),
):
    artifacts = get_frozen_artifacts()
    feature_schema_sha256 = hashlib.sha256(
        "\n".join(artifacts.feature_names).encode("utf-8")
    ).hexdigest()
    return {
        "contract_version": "16e.1",
        "transfer_schema_version": "1.0",
        "evidence_schema_version": "1.0",
        "intelligence_schema_version": "1.0",
        "model_contract": {
            "frozen": True,
            "model_family_version": "rich-v2",
            "main_window_seconds": 60,
            "temporal_window_seconds": 10,
            "feature_count": len(artifacts.feature_names),
            "feature_schema_sha256": feature_schema_sha256,
            "known_class_count": len(artifacts.classes),
            "open_set_label": artifacts.open_set_label,
        },
        "assistant_contract": {
            "contract_version": "16f.1",
            "read_only": True,
            "evidence_schema_version": "1.0",
            "ground_truth_allowed": False,
            "external_context_allowed": False,
            "provider_optional": True,
            "deterministic_fallback": True,
            "supported_focus": [
                "overview",
                "first_failure",
                "funds",
                "recovery",
                "model",
            ],
        },
    }


@router.get("/transfers/{transfer_id}")
async def evidence(transfer_id: str, request: Request, _: str = Depends(authenticated_subject)):
    return await _proxy(
        "transaction", f"/internal/v1/transfers/{transfer_id}", request, method="GET"
    )


@router.get("/transfers/{transfer_id}/evidence", response_model=TransferEvidenceContract)
async def normalized_evidence(
    transfer_id: str,
    request: Request,
    _: str = Depends(authenticated_subject),
):
    return await _proxy(
        "transaction",
        f"/internal/v1/transfers/{transfer_id}/evidence",
        request,
        method="GET",
    )


@router.get("/transfers/{transfer_id}/evidence/export", response_model=TransferEvidenceExport)
async def export_evidence(
    transfer_id: str,
    request: Request,
    _: str = Depends(authenticated_subject),
):
    return await _proxy(
        "transaction",
        f"/internal/v1/transfers/{transfer_id}/evidence/export",
        request,
        method="GET",
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


@router.post(
    "/transfers/{transfer_id}/assistant/explain",
    response_model=IncidentAssistantResponse,
)
async def explain_transfer_case(
    transfer_id: str,
    payload: IncidentAssistantRequest,
    request: Request,
    _: dict = Depends(authenticated_identity),
    assistant: GroundedIncidentAssistant = Depends(get_incident_assistant),
):
    """Explain one authorized case without performing any operational action."""
    normalized = await _proxy(
        "transaction",
        f"/internal/v1/transfers/{transfer_id}/evidence",
        request,
        method="GET",
    )
    raw = await _proxy(
        "transaction",
        f"/internal/v1/transfers/{transfer_id}",
        request,
        method="GET",
    )
    try:
        intelligence = build_transfer_intelligence(
            raw,
            get_live_telemetry_buffer(),
            get_frozen_artifacts(),
        )
    except Exception as exc:  # The evidence explanation must survive optional ML unavailability.
        intelligence = {
            "decision_authority": {
                "workflow_and_journal": "authoritative for transfer outcome and funds state",
                "machine_learning": "temporarily unavailable",
                "ground_truth_used": False,
            },
            "probable_origin": None,
            "model": {
                "status": "UNAVAILABLE",
                "reason": f"frozen model channel unavailable: {type(exc).__name__}",
                "scope": "rolling_system_telemetry",
                "result": None,
            },
        }
    return await assistant.explain(normalized, intelligence, payload)


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
