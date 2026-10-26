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
    build_blind_comparison,
    demo_correlation,
    new_session,
    public_session,
)
from .demo_workload import (
    DemoTelemetryNotReady,
    SustainedDemoWorkload,
    get_demo_workload_runner,
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
        "blind_random_challenge": {
            "enabled": True,
            "selection": "server-side commit/reveal over accepted scenarios",
            "reveal_requires_analysis": True,
            "sustained_observable_workload": True,
            "model_window_required_before_analysis": True,
            "visible_transfer_count": 1,
        },
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
    if session.get("mode") == "BLIND_RANDOM":
        raise HTTPException(
            status_code=409,
            detail="Blind challenge faults are armed internally by the run action",
        )
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


async def _execute_session(
    session_id: str,
    authorization: str,
    store: DemoSessionStore,
    control: DemoFaultControl,
    *,
    clear_fault_after: bool = True,
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    scenario = session["scenario"]
    expected_states = (
        {"PREPARED", "CHALLENGE_RUNNING"} if scenario["fault_id"] is None else {"FAULT_ARMED"}
    )
    if session["state"] not in expected_states:
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
    except Exception:
        if scenario["fault_id"] is not None:
            control.clear(session_id)

        def failure_update(value: dict[str, Any]) -> None:
            value["state"] = "EXECUTION_ERROR"
            if value.get("activation"):
                value["activation"]["cleared_at"] = datetime.now(UTC).isoformat()
            append_demo_event(
                value,
                "transfer_execution_error",
                "FAILED",
                "Execution stopped unexpectedly; the challenge condition was safely cleared.",
            )

        await store.mutate(session_id, failure_update)
        raise
    else:
        if scenario["fault_id"] is not None and clear_fault_after:
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
        if value.get("activation") and clear_fault_after:
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


@router.post("/sessions/{session_id}/execute")
async def execute_transfer(
    session_id: str,
    authorization: str = Header(...),
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
    control: DemoFaultControl = Depends(get_demo_fault_control),
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    if session.get("mode") == "BLIND_RANDOM":
        raise HTTPException(
            status_code=409,
            detail="Use the blind run action so scenario identity remains sealed",
        )
    return await _execute_session(session_id, authorization, store, control)


@router.post("/sessions/{session_id}/run")
async def run_blind_challenge(
    session_id: str,
    authorization: str = Header(...),
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
    control: DemoFaultControl = Depends(get_demo_fault_control),
    workload: SustainedDemoWorkload = Depends(get_demo_workload_runner),
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    if session.get("mode") != "BLIND_RANDOM":
        raise HTTPException(status_code=409, detail="Session is not a blind random challenge")
    if session["state"] != "PREPARED":
        raise HTTPException(status_code=409, detail="Blind challenge has already started")

    profile = workload.profile
    # Preserve legacy short-run behavior; only >=70s uses the audited v1 policy.
    phase19b2_enabled = workload.config.challenge_seconds >= 70
    baseline_started_at = datetime.now(UTC)

    def workload_start(value: dict[str, Any]) -> None:
        if value["state"] != "PREPARED":
            raise ValueError("blind challenge has already started")
        value["state"] = "BASELINE_RUNNING"
        value["snapshots"]["model_workload"] = {
            "status": "RUNNING",
            "profile": profile,
            "phases": [],
        }
        append_demo_event(
            value,
            "blind_workload_started",
            "RUNNING",
            (
                "Observable baseline traffic started. The committed challenge identity "
                "remains sealed."
            ),
        )

    try:
        await store.mutate(session_id, workload_start)
        if phase19b2_enabled:
            # Durable policy prevents a later rolling-window fallback.
            def mark_challenge_only_policy(value: dict[str, Any]) -> None:
                value["snapshots"]["model_window_policy"] = "challenge_only_v1"
            await store.mutate(session_id, mark_challenge_only_policy)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    baseline = await workload.run_phase(session_id, "baseline", workload.config.baseline_seconds)
    try:
        bridge_window = await workload.require_recent_events(baseline_started_at)
    except DemoTelemetryNotReady as exc:
        telemetry_reason = str(exc)

        def telemetry_failure(value: dict[str, Any]) -> None:
            value["state"] = "TELEMETRY_NOT_READY"
            value["analysis_status"] = "TELEMETRY_NOT_READY"
            value["snapshots"]["model_workload"] = {
                "status": "TELEMETRY_NOT_READY",
                "profile": profile,
                "phases": [baseline],
                "reason": telemetry_reason,
            }
            append_demo_event(
                value,
                "telemetry_preflight_failed",
                "BLOCKED",
                "Current service-event telemetry was not available; no challenge ran.",
            )

        await store.mutate(session_id, telemetry_failure)
        raise HTTPException(status_code=409, detail=telemetry_reason) from exc

    session = _session_or_404(store, session_id)
    activation: dict[str, Any] | None = None
    if session["scenario"]["fault_id"] is not None:
        try:
            activation = control.arm(
                session,
                duration_seconds=max(90, round(workload.config.challenge_seconds + 30)),
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        def arm_update(value: dict[str, Any]) -> None:
            value["state"] = "FAULT_ARMED"
            value["activation"] = {
                "activation_id": activation["activation_id"],
                "armed_at": activation["activated_at"],
                "expires_at": activation["expires_at"],
                "cleared_at": None,
            }
            append_demo_event(
                value,
                "blind_challenge_started",
                "RUNNING",
                "The committed condition is active; its identity remains sealed.",
            )

        await store.mutate(session_id, arm_update)
    else:

        def normal_start_update(value: dict[str, Any]) -> None:
            value["state"] = "CHALLENGE_RUNNING"
            append_demo_event(
                value,
                "blind_challenge_started",
                "RUNNING",
                "The committed condition is active; its identity remains sealed.",
            )

        await store.mutate(session_id, normal_start_update)

    phase19b2_model: dict[str, Any] | None = None
    phase19b2_reason: str | None = None
    try:
        execution = await _execute_session(
            session_id,
            authorization,
            store,
            control,
            clear_fault_after=False,
        )
        challenge = await workload.run_phase(
            session_id, "challenge", workload.config.challenge_seconds
        )
        # The frozen model sees only observables. Evaluate before cleanup/recovery.
        if phase19b2_enabled:
            try:
                from .phase19b2_window import infer_observable
                bounded_snapshot = await workload.require_challenge_model_window(challenge)
                candidate_model = infer_observable(bounded_snapshot, get_frozen_artifacts())
                # Blind observational proof only. No scenario/label/fault-control access.
                from .phase19b3_observability import collect_observable_evidence
                candidate_model["observability"] = collect_observable_evidence(
                    workload.buffer, session_id, bounded_snapshot
                )
                def preserve_model_before_recovery(value: dict[str, Any]) -> None:
                    value["snapshots"]["phase19b2_challenge_model"] = candidate_model
                await store.mutate(session_id, preserve_model_before_recovery)
                phase19b2_model = candidate_model
            except (DemoTelemetryNotReady, RuntimeError, ValueError) as exc:
                phase19b2_reason = f"{type(exc).__name__}: {exc}"
    finally:
        if activation is not None:
            control.clear(session_id)

            def cleared(value: dict[str, Any]) -> None:
                if value.get("activation"):
                    value["activation"]["cleared_at"] = datetime.now(UTC).isoformat()

            await store.mutate(session_id, cleared)

    recovery = await workload.run_phase(session_id, "recovery", workload.config.recovery_seconds)
    try:
        if phase19b2_enabled:
            if phase19b2_model is None:
                raise DemoTelemetryNotReady(
                    phase19b2_reason or "challenge-only v1 inference was unavailable"
                )
            model_window = phase19b2_model["window"]
        else:
            model_window = await workload.require_model_window()
    except DemoTelemetryNotReady as exc:
        window_reason = str(exc)

        def window_failure(value: dict[str, Any]) -> None:
            value["analysis_status"] = "TELEMETRY_NOT_READY"
            value["snapshots"]["model_workload"] = {
                "status": "TELEMETRY_NOT_READY",
                "profile": profile,
                "phases": [baseline, challenge, recovery],
                "bridge_preflight": bridge_window,
                "reason": window_reason,
            }
            append_demo_event(
                value,
                "model_window_not_ready",
                "BLOCKED",
                "The transfer evidence is safe, but a complete model window is unavailable.",
            )

        await store.mutate(session_id, window_failure)
        raise HTTPException(status_code=409, detail=window_reason) from exc

    def workload_ready(value: dict[str, Any]) -> None:
        value["analysis_status"] = "WINDOW_READY"
        value["snapshots"]["model_workload"] = {
            "status": "READY",
            "profile": profile,
            "phases": [baseline, challenge, recovery],
            "bridge_preflight": bridge_window,
            "model_window": model_window,
            "ground_truth_used": False,
        }
        append_demo_event(
            value,
            "model_window_ready",
            "READY",
            "A genuine 60-second observable window is ready for frozen-model analysis.",
        )

    updated = await store.mutate(session_id, workload_ready)
    return {
        "session": public_session(updated),
        "evidence": execution["evidence"],
        "workload": updated["snapshots"]["model_workload"],
    }


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
    saved_snapshot = session.get("snapshots") or {}
    saved_model = saved_snapshot.get("phase19b2_challenge_model")
    if saved_snapshot.get("model_window_policy") == "challenge_only_v1" and (
        not isinstance(saved_model, dict) or saved_model.get("status") != "EVALUATED"
    ):
        raise HTTPException(status_code=409, detail="Challenge-only model decision not ready")
    try:
        if saved_model is None:
            intelligence = build_transfer_intelligence(
                raw, get_live_telemetry_buffer(), get_frozen_artifacts()
            )
        else:
            intelligence = build_transfer_intelligence(
                raw, get_live_telemetry_buffer(), get_frozen_artifacts(),
                model_override=saved_model,
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
    if session.get("mode") == "BLIND_RANDOM" and intelligence["model"]["status"] != "EVALUATED":
        raise HTTPException(
            status_code=409,
            detail=(
                "Blind challenge analysis requires an evaluated frozen-model window; "
                f"observed {intelligence['model']['status']}"
            ),
        )
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


@router.post("/sessions/{session_id}/reveal")
async def reveal_blind_challenge(
    session_id: str,
    _: dict = Depends(_engineer),
    store: DemoSessionStore = Depends(get_demo_store),
) -> dict[str, Any]:
    session = _session_or_404(store, session_id)
    if session.get("mode") != "BLIND_RANDOM":
        raise HTTPException(status_code=409, detail="Session is not a blind random challenge")
    if session.get("analysis_status") != "READY":
        raise HTTPException(
            status_code=409,
            detail="Complete evidence analysis before revealing the committed scenario",
        )
    if session["challenge"].get("revealed_at") is not None:
        return {
            "session": public_session(session),
            "comparison": session["snapshots"]["challenge_comparison"],
        }
    try:
        comparison = build_blind_comparison(session)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    revealed_at = datetime.now(UTC).isoformat()

    def update(value: dict[str, Any]) -> None:
        value["challenge"]["revealed_at"] = revealed_at
        value["challenge"]["revealed_nonce"] = value["challenge"].pop("secret_nonce")
        value["snapshots"]["challenge_comparison"] = comparison
        append_demo_event(
            value,
            "challenge_revealed",
            "VERIFIED",
            (
                "The committed scenario was revealed after analysis; its cryptographic "
                "commitment was verified."
            ),
        )

    updated = await store.mutate(session_id, update)
    return {"session": public_session(updated), "comparison": comparison}


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
