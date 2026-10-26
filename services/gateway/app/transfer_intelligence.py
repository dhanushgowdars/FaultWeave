"""Evidence-first intelligence for one durable banking transfer."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from .intelligence_artifacts import FrozenIntelligenceArtifacts
from .intelligence_inference import IntelligenceInferenceRequest, run_intelligence_inference
from .intelligence_live import LiveTelemetryBuffer, LiveWindowNotReady
from .phase19b1_capture import capture_if_enabled


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("transfer evidence timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("transfer evidence timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _event_interval(evidence: dict[str, Any]) -> tuple[datetime, datetime] | None:
    timestamps = []
    for event in evidence.get("timeline", []):
        try:
            timestamps.append(_timestamp(event.get("timestamp")))
        except ValueError:
            continue
    if not timestamps:
        return None
    return min(timestamps), max(timestamps)


def _window_overlaps_transfer(
    snapshot: dict[str, Any], interval: tuple[datetime, datetime] | None
) -> bool:
    if interval is None:
        return False
    window_start = _timestamp(snapshot["window_started_at"])
    window_end = _timestamp(snapshot["window_ended_at"])
    transfer_start, transfer_end = interval
    return transfer_start <= window_end and transfer_end >= window_start


def _failure_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    history = evidence.get("case_history") or {}
    failures = list(history.get("failures") or [])
    reviews = list(history.get("reviews") or [])
    recoveries = list(history.get("recoveries") or [])
    investigation = evidence.get("investigation") or {}
    latest = failures[-1] if failures else None
    error = (latest or {}).get("error") or investigation.get("observed_error") or {}
    service = (latest or {}).get("service") or investigation.get("observed_failure_service")
    step = (latest or {}).get("step") or error.get("step")
    return {
        "failure_observed": bool(failures or error),
        "observed_service": service,
        "observed_step": step,
        "error_type": error.get("error_type"),
        "error_detail": error.get("detail"),
        "funds_status": evidence.get("funds_status", "UNKNOWN"),
        "review_count": len(reviews),
        "recovery_count": len(recoveries),
        "recovered_after_review": bool(
            evidence.get("state") == "COMPLETED" and failures and recoveries
        ),
        "evidence_event_ids": list(investigation.get("evidence_event_ids") or []),
    }


def _explanation(evidence: dict[str, Any], facts: dict[str, Any]) -> dict[str, str]:
    state = str(evidence.get("state") or "UNKNOWN")
    service = facts["observed_service"] or "a downstream dependency"
    step = facts["observed_step"] or "transfer processing"
    funds = facts["funds_status"]
    if not facts["failure_observed"]:
        return {
            "summary": "The authenticated transfer completed without recorded workflow failure.",
            "what_broke": "No failed workflow step was recorded.",
            "what_happened_next": (
                "Payment, account movement and ledger evidence completed normally."
            ),
            "funds": "The balanced debit and credit journal confirms one funds movement.",
            "engineer_action": "No recovery action is required; retain the evidence for audit.",
        }
    if state == "REQUIRES_REVIEW":
        next_step = (
            "The transfer is intentionally paused. An engineer must inspect the stored evidence, "
            "restore the dependency and resume this same transfer identity."
        )
    else:
        next_step = (
            "An engineer reviewed the stored evidence, restored the dependency and resumed the "
            "same idempotent transfer; recovery was then verified."
        )
    funds_text = {
        "COMMITTED": "Funds were already committed; retrying as a new transfer would be unsafe.",
        "NOT_COMMITTED": "The failure happened before funds movement was committed.",
        "NOT_ATTEMPTED": "The payment layer had not attempted account movement.",
        "UNKNOWN": "The funds outcome was initially uncertain and required reconciliation.",
    }.get(funds, "The funds state must be checked from the journal evidence.")
    return {
        "summary": f"A failure was observed at {service} during {step}.",
        "what_broke": str(
            facts["error_detail"]
            or f"The {service} dependency did not provide a confirmed successful outcome."
        ),
        "what_happened_next": next_step,
        "funds": funds_text,
        "engineer_action": str(
            (evidence.get("investigation") or {}).get("next_action")
            or "Inspect the cited event IDs before deciding whether to resume."
        ),
    }


def _model_channel(
    evidence: dict[str, Any],
    buffer: LiveTelemetryBuffer,
    artifacts: FrozenIntelligenceArtifacts,
) -> dict[str, Any]:
    try:
        snapshot = buffer.snapshot(60, require_client_requests=True)
    except LiveWindowNotReady as exc:
        return {
            "status": "WINDOW_NOT_READY",
            "reason": str(exc),
            "scope": "rolling_system_telemetry",
            "result": None,
        }
    if not _window_overlaps_transfer(snapshot, _event_interval(evidence)):
        return {
            "status": "WINDOW_NOT_CORRELATED",
            "reason": "The latest validated 60-second window does not overlap this transfer.",
            "scope": "rolling_system_telemetry",
            "window": snapshot,
            "result": None,
        }
    result = run_intelligence_inference(
        artifacts,
        IntelligenceInferenceRequest(
            window_seconds=60,
            features=snapshot["features"],
        ),
    )
    # Capture exactly the vector used above; keep diagnostics out of the API/AI payload.
    try:
        capture_if_enabled(snapshot, result, artifacts.feature_names)
    except (OSError, ValueError, TypeError) as exc:
        # An optional audit must never alter banking behavior or model results.
        logging.getLogger(__name__).warning(
            "Phase 19B.1 capture unavailable: %s", type(exc).__name__
        )
    return {
        "status": "EVALUATED",
        "reason": None,
        "scope": "rolling_system_telemetry",
        "window": {key: value for key, value in snapshot.items() if key not in {"features"}},
        "feature_schema_sha256": hashlib.sha256(
            "\n".join(artifacts.feature_names).encode("utf-8")
        ).hexdigest(),
        "result": result,
    }


def build_transfer_intelligence(
    evidence: dict[str, Any],
    buffer: LiveTelemetryBuffer,
    artifacts: FrozenIntelligenceArtifacts,
    *,
    model_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Join authoritative workflow evidence with an independently valid ML window."""
    facts = _failure_summary(evidence)
    origin = None
    if facts["failure_observed"]:
        origin = {
            "status": "OBSERVED_IN_WORKFLOW_EVIDENCE",
            "service": facts["observed_service"],
            "step": facts["observed_step"],
            "basis": "persisted failure history and ordered workflow events",
            "event_ids": facts["evidence_event_ids"],
        }
    return {
        "schema_version": "1.0",
        "transfer_id": evidence.get("id"),
        "transfer_state": evidence.get("state"),
        "decision_authority": {
            "workflow_and_journal": "authoritative for transfer outcome and funds state",
            "machine_learning": (
                "independent challenge-only frozen-model evidence"
                if model_override is not None else "independent rolling-system anomaly evidence"
            ),
            "ground_truth_used": False,
        },
        "operational_facts": facts,
        "probable_origin": origin,
        "explanation": _explanation(evidence, facts),
        "model": model_override if model_override is not None else _model_channel(evidence, buffer, artifacts),
        "timeline": evidence.get("timeline", []),
        "case_history": evidence.get("case_history", {}),
    }
