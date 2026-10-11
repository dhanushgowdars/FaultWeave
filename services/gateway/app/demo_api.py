"""Engineer-controlled API for one ordered, evidence-backed live demonstration."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from faultweave_common.schemas import IncidentAssistantRequest
from faultweave_common.security import authenticated_identity
from faultweave_common.transfer_http import call_service

from .demo_orchestration import (
    DEMO_CONTRACT_VERSION,
    DEMO_SCENARIOS,
    DemoApproval,
    DemoFaultControl,
    DemoSessionCreate,
    DemoSessionStore,
    append_demo_event,
    demo_correlation,
    new_session,
    public_session,
)
from .incident_assistant import GroundedIncidentAssistant, get_incident_assistant
from .intelligence_artifacts import get_frozen_artifacts
from .intelligence_live import get_live_telemetry_buffer
from .transfer_intelligence import build_transfer_intelligence

router = APIRouter(prefix="/api/v1/demo", tags=["demo-orchestration"])


@lru_cache(maxsize=1)
def get_demo_store() -> DemoSessionStore:
    return DemoSessionStore()


@lru_cache(maxsize=1)
def get_demo_fault_control() -> DemoFaultControl:
    return DemoFaultControl()


def _engineer(identity: dict = Depends(authenticated_identity)) -> dict:
    if identity["role"] != "engineer":
        raise HTTPException(status_code=403, detail="Engineer role required")
    return identity


async def _service(
    target: str,
    path: str,
    authorization: str | None,
    payload: dict[str, Any] | None = None,
    method: str = "POST",
) -> dict[str, Any]:
    try:
        return await call_service(
            "gateway", target, path, authorization, payload=payload, method=method
        )
    except httpx.HTTPStatusError as exc:
        try:
            detail = str(exc.response.json().get("detail") or "Downstream operation rejected")
        except ValueError:
            detail = "Downstream operation returned invalid evidence"
        raise HTTPException(
            status_code=exc.response.status_code if exc.response.status_code < 500 else 502,
            detail=detail,
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503, detail="Required backend service is unavailable"
        ) from exc


async def _demo_customer_authorization() -> str:
    login = await _service(
        "authentication",
        "/internal/v1/auth/login",
        None,
        {
            "username": os.getenv("DEMO_USERNAME", "demo"),
            "password": os.getenv("DEMO_PASSWORD", "faultweave-demo"),
        },
    )
    return f"Bearer {login['access_token']}"


def _session_or_404(store: DemoSessionStore, session_id: str) -> dict[str, Any]:
    try:
        return store.load(session_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Demo session not found") from exc


@router.get("/contract")
async def contract(_: dict = Depends(_engineer)) -> dict[str, Any]:
    return {
        "contract_version": DEMO_CONTRACT_VERSION,
        "business_records": "PostgreSQL transfer, account, payment and ledger evidence",
        "control_records": "Restart-persistent demo sequencing without financial authority",
        "automatic_recovery": False,
        "ground_truth_used_by_model_or_assistant": False,
        "ordered_actions": [
            "prepare",
            "arm_fault",
            "execute_transfer",
            "inspect_and_explain",
            "approve",
            "recover_and_verify",
        ],
    }


@router.get("/scenarios")
async def scenarios(_: dict = Depends(_engineer)) -> dict[str, Any]:
    return {
        "contract_version": DEMO_CONTRACT_VERSION,
        "items": [item.model_dump() for item in DEMO_SCENARIOS.values()],
    }


@router.post("/sessions", status_code=201)
async def create_session(
    payload: DemoSessionCreate,
    identity: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
) -> dict[str, Any]:
    try:
        session = new_session(payload, identity["subject"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.save(session)
    return public_session(session)


@router.get("/sessions")
async def list_sessions(
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    return {
        "contract_version": DEMO_CONTRACT_VERSION,
        "items": [public_session(item) for item in store.list(limit=limit)],
    }


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
) -> dict[str, Any]:
    return public_session(_session_or_404(store, session_id))


@router.post("/sessions/{session_id}/arm")
async def arm_fault(
    session_id: str,
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
    control: DemoFaultControl = Depends(get_demo_fault_control),
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    if session["state"] not in {"PREPARED", "FAULT_ARMED"}:
        raise HTTPException(status_code=409, detail="Fault cannot be armed from current state")
    if session["scenario"]["fault_id"] is None:
        raise HTTPException(status_code=409, detail="Normal scenario does not inject a fault")
    try:
        activation = control.arm(session)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def update(value: dict[str, Any]) -> None:
        value["state"] = "FAULT_ARMED"
        value["activation"] = {
            "activation_id": activation["activation_id"],
            "armed_at": activation["activated_at"],
            "expires_at": activation["expires_at"],
            "cleared_at": None,
        }
        append_demo_event(
            value,
            "fault_armed",
            "READY",
            "Controlled fault armed for this session; no transfer has run yet.",
        )

    return public_session(await store.mutate(session_id, update))


async def _normalized_evidence(transfer_id: str, authorization: str) -> dict[str, Any]:
    return await _service(
        "transaction",
        f"/internal/v1/transfers/{transfer_id}/evidence",
        authorization,
        method="GET",
    )


async def _raw_evidence(transfer_id: str, authorization: str) -> dict[str, Any]:
    return await _service(
        "transaction",
        f"/internal/v1/transfers/{transfer_id}",
        authorization,
        method="GET",
    )


@router.post("/sessions/{session_id}/execute")
async def execute_transfer(
    session_id: str,
    authorization: str = Header(...),
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
    control: DemoFaultControl = Depends(get_demo_fault_control),
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    scenario = session["scenario"]
    expected_state = "PREPARED" if scenario["fault_id"] is None else "FAULT_ARMED"
    if session["state"] != expected_state:
        raise HTTPException(status_code=409, detail="Transfer cannot execute from current state")
    if scenario["fault_id"] is not None:
        try:
            control.require_owned(session_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    transfer = session["transfer"]
    started: dict[str, Any] | None = None
    sender_before: dict[str, Any] | None = None
    receiver_before: dict[str, Any] | None = None
    try:
        customer_authorization = await _demo_customer_authorization()
        with demo_correlation(session_id, "setup", fault_scope=False):
            sender_before = await _service(
                "account",
                "/internal/v1/accounts/lookup",
                customer_authorization,
                {"account_number": transfer["source_account_number"]},
            )
            receiver_before = await _service(
                "account",
                "/internal/v1/accounts/demo/create",
                customer_authorization,
                {"account_number": transfer["destination_account_number"]},
            )
        with demo_correlation(
            session_id,
            "execute",
            force_error_bucket=scenario["fault_id"] == "DOWNSTREAM_ERROR_BURST",
        ):
            started = await _service(
                "transaction",
                "/internal/v1/transfers",
                customer_authorization,
                transfer,
            )
    finally:
        if scenario["fault_id"] is not None:
            control.clear(session_id)

    if started is None:
        raise HTTPException(status_code=502, detail="Transfer execution returned no durable result")
    transfer_id = str(started["id"])
    evidence = await _normalized_evidence(transfer_id, authorization)
    if scenario["fault_id"] is None:
        if evidence["state"] != "COMPLETED" or evidence["funds_status"] != "COMMITTED":
            raise HTTPException(status_code=409, detail="Normal transfer did not complete")
        next_state = "COMPLETED"
    else:
        failure = (evidence.get("case_history", {}).get("failures") or [{}])[-1]
        observed_step = failure.get("step")
        if (
            evidence["state"] != "REQUIRES_REVIEW"
            or observed_step != scenario["expected_step"]
            or evidence["funds_status"] != scenario["expected_funds_status"]
        ):
            raise HTTPException(
                status_code=409,
                detail="Observed transfer evidence did not match the controlled scenario contract",
            )
        next_state = "AWAITING_REVIEW"

    def update(value: dict[str, Any]) -> None:
        value["transfer_id"] = transfer_id
        value["state"] = next_state
        value["snapshots"]["balances_before"] = {
            "source": sender_before,
            "destination": receiver_before,
        }
        value["snapshots"]["evidence_after_execution"] = evidence
        if value.get("activation"):
            value["activation"]["cleared_at"] = datetime.now(UTC).isoformat()
        append_demo_event(
            value,
            "transfer_executed",
            "SUCCEEDED" if next_state == "COMPLETED" else "REQUIRES_REVIEW",
            (
                "Normal transfer completed with committed funds and journal evidence."
                if next_state == "COMPLETED"
                else "Failure evidence persisted; fault cleared but transfer remains paused."
            ),
        )

    updated = await store.mutate(session_id, update)
    return {"session": public_session(updated), "evidence": evidence}


@router.post("/sessions/{session_id}/analyze")
async def analyze_session(
    session_id: str,
    authorization: str = Header(...),
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
    assistant: GroundedIncidentAssistant = Depends(get_incident_assistant),
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    if session["state"] not in {"AWAITING_REVIEW", "APPROVED", "RECOVERED", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="No executed transfer is available to analyze")
    transfer_id = session["transfer_id"]
    normalized = await _normalized_evidence(transfer_id, authorization)
    raw = await _raw_evidence(transfer_id, authorization)
    try:
        intelligence = build_transfer_intelligence(
            raw, get_live_telemetry_buffer(), get_frozen_artifacts()
        )
    except Exception as exc:
        intelligence = {
            "decision_authority": {
                "workflow_and_journal": "authoritative",
                "machine_learning": "temporarily unavailable",
                "ground_truth_used": False,
            },
            "model": {"status": "UNAVAILABLE", "reason": type(exc).__name__, "result": None},
        }
    explanation = await assistant.explain(
        normalized, intelligence, IncidentAssistantRequest(focus="overview")
    )
    # GroundedIncidentAssistant returns its validated API-ready dictionary.
    # Keep compatibility with protocol-conforming test/providers that return a
    # Pydantic response object, but never require a second serialization pass.
    assistant_json = (
        explanation.model_dump(mode="json") if hasattr(explanation, "model_dump") else explanation
    )

    def update(value: dict[str, Any]) -> None:
        value["analysis_status"] = "READY"
        value["snapshots"]["latest_evidence"] = normalized
        value["snapshots"]["intelligence"] = intelligence
        value["snapshots"]["assistant"] = assistant_json
        append_demo_event(
            value,
            "case_analyzed",
            "READY",
            "Evidence, frozen-model channel and grounded explanation are ready for review.",
        )

    updated = await store.mutate(session_id, update)
    return {
        "session": public_session(updated),
        "evidence": normalized,
        "intelligence": intelligence,
        "assistant": assistant_json,
    }


@router.post("/sessions/{session_id}/approve")
async def approve_recovery(
    session_id: str,
    payload: DemoApproval,
    identity: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    if session["state"] != "AWAITING_REVIEW":
        raise HTTPException(status_code=409, detail="Only a paused case can be approved")
    if session["analysis_status"] != "READY":
        raise HTTPException(status_code=409, detail="Inspect and explain evidence before approval")

    def update(value: dict[str, Any]) -> None:
        value["state"] = "APPROVED"
        value["approval"] = {
            "decision": "RESUME_SAME_TRANSFER",
            "note": payload.note,
            "approved_by": identity["subject"],
            "approved_at": value["updated_at"],
        }
        append_demo_event(
            value,
            "recovery_approved",
            "APPROVED",
            "Engineer approved an idempotent resume; recovery has not run yet.",
        )

    return public_session(await store.mutate(session_id, update))


@router.post("/sessions/{session_id}/recover")
async def recover_session(
    session_id: str,
    authorization: str = Header(...),
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
    control: DemoFaultControl = Depends(get_demo_fault_control),
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    if session["state"] != "APPROVED":
        raise HTTPException(status_code=409, detail="Engineer approval is required before recovery")
    if control.read() is not None:
        raise HTTPException(status_code=409, detail="Clear the active fault before recovery")
    transfer_id = session["transfer_id"]
    with demo_correlation(session_id, "recover"):
        resumed = await _service(
            "transaction",
            f"/internal/v1/transfers/{transfer_id}/resume",
            authorization,
            {"note": session["approval"]["note"]},
        )
    if resumed["state"] != "COMPLETED":
        raise HTTPException(status_code=409, detail="Reviewed transfer did not complete")
    evidence = await _normalized_evidence(transfer_id, authorization)
    customer_authorization = await _demo_customer_authorization()
    transfer = session["transfer"]
    source_after = await _service(
        "account",
        "/internal/v1/accounts/lookup",
        customer_authorization,
        {"account_number": transfer["source_account_number"]},
    )
    destination_after = await _service(
        "account",
        "/internal/v1/accounts/lookup",
        customer_authorization,
        {"account_number": transfer["destination_account_number"]},
    )
    before = session["snapshots"]["balances_before"]
    amount = transfer["amount_minor"]
    journal_entries = (evidence.get("journal") or {}).get("entries") or []
    verified = (
        evidence["state"] == "COMPLETED"
        and evidence["funds_status"] == "COMMITTED"
        and bool(evidence["case_history"]["reviews"])
        and bool(evidence["case_history"]["recoveries"])
        and source_after["balance_minor"] == before["source"]["balance_minor"] - amount
        and destination_after["balance_minor"] == before["destination"]["balance_minor"] + amount
        and len(journal_entries) == 2
        and {entry["direction"] for entry in journal_entries} == {"DEBIT", "CREDIT"}
    )
    if not verified:
        raise HTTPException(
            status_code=409, detail="Recovery evidence did not pass financial checks"
        )

    def update(value: dict[str, Any]) -> None:
        value["state"] = "RECOVERED"
        value["verification"] = {
            "status": "VERIFIED",
            "same_transfer_id": transfer_id,
            "single_funds_movement": True,
            "balanced_journal": True,
            "source_balance_minor": source_after["balance_minor"],
            "destination_balance_minor": destination_after["balance_minor"],
        }
        value["snapshots"]["evidence_after_recovery"] = evidence
        value["snapshots"]["balances_after"] = {
            "source": source_after,
            "destination": destination_after,
        }
        append_demo_event(
            value,
            "recovery_verified",
            "VERIFIED",
            "Same transfer completed once; balances and debit/credit journal reconcile.",
        )

    updated = await store.mutate(session_id, update)
    return {"session": public_session(updated), "evidence": evidence}
