"""Persisted, evidence-first orchestration for the judge-facing live demo."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal
from uuid import uuid4

from faultweave_common.logging import reset_correlation_context, set_correlation_context
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEMO_CONTRACT_VERSION = "19.0"
DEMO_ROOT = Path(os.getenv("FAULTWEAVE_DEMO_ROOT", "/faultweave-demo"))
FAULT_CONTROL_PATH = Path(os.getenv("FAULT_CONTROL_PATH", "/faultweave-control/active_fault.json"))
BLIND_CHALLENGE_VERSION = "phase19a-v1"
BLIND_SCENARIO_IDS = (
    "normal-transfer",
    "payment-service-unavailable",
    "ledger-service-unavailable",
    "account-response-timeout",
    "ledger-response-timeout",
    "account-error-response",
)


class DemoScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    title: str
    summary: str
    fault_id: str | None = None
    target: str | None = None
    intensity: str | None = None
    expected_step: str | None = None
    expected_funds_status: str
    recommended: bool = False


def _scenario(**values: Any) -> DemoScenario:
    return DemoScenario(**values)


DEMO_SCENARIOS = MappingProxyType(
    {
        "normal-transfer": _scenario(
            scenario_id="normal-transfer",
            title="Successful account-to-account transfer",
            summary="Authenticate, validate, move funds once, post a balanced journal and finish.",
            expected_funds_status="COMMITTED",
            recommended=True,
        ),
        "payment-service-unavailable": _scenario(
            scenario_id="payment-service-unavailable",
            title="Payment service unavailable",
            summary="Transaction cannot confirm that Payment accepted the instruction.",
            fault_id="SERVICE_UNAVAILABLE",
            target="payment",
            intensity="high",
            expected_step="payment_execution",
            expected_funds_status="UNKNOWN",
            recommended=True,
        ),
        "ledger-service-unavailable": _scenario(
            scenario_id="ledger-service-unavailable",
            title="Ledger unavailable after funds commit",
            summary="Funds commit once, but Payment cannot obtain the journal confirmation.",
            fault_id="SERVICE_UNAVAILABLE",
            target="ledger",
            intensity="high",
            expected_step="ledger_posting",
            expected_funds_status="COMMITTED",
            recommended=True,
        ),
        "account-response-timeout": _scenario(
            scenario_id="account-response-timeout",
            title="Account dependency timeout",
            summary="Payment cannot confirm the account commit outcome and pauses for review.",
            fault_id="DOWNSTREAM_TIMEOUT",
            target="payment->account",
            intensity="medium",
            expected_step="account_commit",
            expected_funds_status="UNKNOWN",
        ),
        "ledger-response-timeout": _scenario(
            scenario_id="ledger-response-timeout",
            title="Ledger confirmation timeout",
            summary="Committed funds are preserved while the journal confirmation is reconciled.",
            fault_id="DOWNSTREAM_TIMEOUT",
            target="payment->ledger",
            intensity="medium",
            expected_step="ledger_posting",
            expected_funds_status="COMMITTED",
        ),
        "account-error-response": _scenario(
            scenario_id="account-error-response",
            title="Account service error response",
            summary="A controlled account error stops the workflow before commit is confirmed.",
            fault_id="DOWNSTREAM_ERROR_BURST",
            target="account",
            intensity="high",
            expected_step="account_commit",
            expected_funds_status="UNKNOWN",
        ),
    }
)


class DemoSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["CONTROLLED", "BLIND_RANDOM"] = "CONTROLLED"
    scenario_id: str | None = None
    source_account_number: str = Field(default="FW-DEMO-001", min_length=3, max_length=40)
    destination_account_number: str | None = Field(default=None, min_length=3, max_length=40)
    amount_minor: int = Field(default=1000, gt=0, le=100_000_000)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    sender_name: str = Field(default="Demo sender", min_length=2, max_length=80)
    recipient_name: str = Field(default="Demo recipient", min_length=2, max_length=80)
    payment_channel: Literal["UPI", "DEBIT_CARD", "NET_BANKING"] = "UPI"
    purpose: str = Field(default="FaultWeave demonstration", min_length=2, max_length=120)

    @field_validator("sender_name", "recipient_name", "purpose", mode="before")
    @classmethod
    def normalize_presentation_text(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_mode_and_scenario(self) -> DemoSessionCreate:
        if self.mode == "CONTROLLED" and not self.scenario_id:
            raise ValueError("controlled mode requires scenario_id")
        if self.mode == "BLIND_RANDOM" and self.scenario_id is not None:
            raise ValueError("blind random mode selects the scenario on the server")
        return self


class DemoApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=10, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class DemoSessionStore:
    """Atomic JSON persistence for non-financial demo control metadata."""

    def __init__(self, root: Path = DEMO_ROOT) -> None:
        self.root = root
        self._lock = asyncio.Lock()

    def _path(self, session_id: str) -> Path:
        if not session_id.startswith("demo-") or not session_id[5:].isalnum():
            raise ValueError("invalid demo session identifier")
        return self.root / "sessions" / f"{session_id}.json"

    def load(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(session_id) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("stored demo session is invalid") from exc
        if value.get("session_id") != session_id:
            raise RuntimeError("stored demo session identity changed")
        return value

    def save(self, session: dict[str, Any]) -> dict[str, Any]:
        session_id = str(session["session_id"])
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        session["updated_at"] = datetime.now(UTC).isoformat()
        encoded = json.dumps(session, indent=2, sort_keys=True) + "\n"
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(encoded, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
        return session

    def list(self, *, limit: int = 25) -> list[dict[str, Any]]:
        """Return recent, valid demo sessions without inventing presentation history."""
        sessions: list[dict[str, Any]] = []
        directory = self.root / "sessions"
        if not directory.exists():
            return sessions
        for path in directory.glob("demo-*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict) or value.get("session_id") != path.stem:
                continue
            sessions.append(value)
        sessions.sort(
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )
        return sessions[:limit]

    async def mutate(self, session_id: str, operation) -> dict[str, Any]:
        async with self._lock:
            session = self.load(session_id)
            result = operation(session)
            if result is not None:
                session = result
            return self.save(session)


def _blind_scenario_id(secret_nonce: str) -> str:
    digest = hashlib.sha256(f"{BLIND_CHALLENGE_VERSION}:{secret_nonce}".encode()).digest()
    return BLIND_SCENARIO_IDS[int.from_bytes(digest[:8], "big") % len(BLIND_SCENARIO_IDS)]


def _blind_commitment(secret_nonce: str, scenario_id: str) -> str:
    return hashlib.sha256(
        f"{BLIND_CHALLENGE_VERSION}:{secret_nonce}:{scenario_id}".encode()
    ).hexdigest()


def verify_blind_commitment(challenge: dict[str, Any], scenario_id: str) -> bool:
    nonce = challenge.get("secret_nonce") or challenge.get("revealed_nonce")
    if not isinstance(nonce, str):
        return False
    return secrets.compare_digest(
        str(challenge.get("commitment_sha256") or ""),
        _blind_commitment(nonce, scenario_id),
    )


def new_session(payload: DemoSessionCreate, created_by: str) -> dict[str, Any]:
    challenge: dict[str, Any] | None = None
    if payload.mode == "BLIND_RANDOM":
        secret_nonce = secrets.token_hex(32)
        scenario_id = _blind_scenario_id(secret_nonce)
        scenario = DEMO_SCENARIOS[scenario_id]
        challenge = {
            "selector_version": BLIND_CHALLENGE_VERSION,
            "pool_size": len(BLIND_SCENARIO_IDS),
            "commitment_sha256": _blind_commitment(secret_nonce, scenario_id),
            "secret_nonce": secret_nonce,
            "revealed_at": None,
        }
    else:
        try:
            scenario = DEMO_SCENARIOS[str(payload.scenario_id)]
        except KeyError as exc:
            raise ValueError("unsupported demo scenario") from exc
    now = datetime.now(UTC).isoformat()
    session_id = f"demo-{uuid4().hex[:16]}"
    destination = payload.destination_account_number or f"FW-{session_id[5:].upper()}"
    session = {
        "contract_version": DEMO_CONTRACT_VERSION,
        "session_id": session_id,
        "mode": payload.mode,
        "state": "PREPARED",
        "scenario": scenario.model_dump(),
        "challenge": challenge,
        "transfer": {
            "source_account_number": payload.source_account_number,
            "destination_account_number": destination,
            "amount_minor": payload.amount_minor,
            "currency": payload.currency,
            "idempotency_key": f"{session_id}-transfer",
        },
        "presentation": {
            "sender_name": payload.sender_name,
            "recipient_name": payload.recipient_name,
            "payment_channel": payload.payment_channel,
            "purpose": payload.purpose,
        },
        "transfer_id": None,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
        "analysis_status": "NOT_REQUESTED",
        "approval": None,
        "verification": None,
        "snapshots": {},
        "events": [],
    }
    append_demo_event(
        session,
        "session_prepared",
        "READY",
        (
            "Blind challenge committed on the server; its scenario remains sealed."
            if payload.mode == "BLIND_RANDOM"
            else "Demo case prepared; no transfer or fault has executed yet."
        ),
    )
    return session


def append_demo_event(
    session: dict[str, Any], action: str, status: str, detail: str
) -> dict[str, Any]:
    events = session.setdefault("events", [])
    event = {
        "sequence": len(events) + 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "status": status,
        "detail": detail,
    }
    events.append(event)
    return event


class DemoFaultControl:
    """Exclusive, bounded fault activation shared with the application services."""

    def __init__(self, path: Path = FAULT_CONTROL_PATH) -> None:
        self.path = path

    def read(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            expires = datetime.fromisoformat(str(value["expires_at"]).replace("Z", "+00:00"))
        except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
            return None
        if expires <= datetime.now(UTC):
            self.path.unlink(missing_ok=True)
            return None
        return value

    def arm(self, session: dict[str, Any], *, duration_seconds: int = 90) -> dict[str, Any]:
        scenario = session["scenario"]
        if scenario["fault_id"] is None:
            raise ValueError("normal scenario has no fault to arm")
        active = self.read()
        if active is not None and active.get("run_id") != session["session_id"]:
            raise RuntimeError("another controlled fault is already active")
        now = datetime.now(UTC)
        activation = {
            "schema_version": "1.0",
            "activation_id": uuid4().hex,
            "run_id": session["session_id"],
            "fault_id": scenario["fault_id"],
            "target": scenario["target"],
            "intensity": scenario["intensity"],
            "duration_seconds": duration_seconds,
            "activated_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=duration_seconds)).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(activation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if active is None and self.path.exists():
            temporary.unlink(missing_ok=True)
            raise RuntimeError("another controlled fault became active")
        os.replace(temporary, self.path)
        return activation

    def require_owned(self, session_id: str) -> dict[str, Any]:
        active = self.read()
        if active is None or active.get("run_id") != session_id:
            raise RuntimeError("demo fault is not armed or has expired")
        return active

    def clear(self, session_id: str) -> None:
        active = self.read()
        if active is None:
            return
        if active.get("run_id") != session_id:
            raise RuntimeError("refusing to clear another session's fault")
        self.path.unlink(missing_ok=True)


def _request_id(session_id: str, action: str, *, force_error_bucket: bool) -> str:
    for sequence in range(1, 10_000):
        candidate = f"{session_id}-{action}-{sequence:04d}"
        bucket = int(hashlib.sha256(candidate.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if not force_error_bucket or bucket < 0.9:
            return candidate
    raise RuntimeError("unable to create a controlled request identity")


@contextmanager
def demo_correlation(
    session_id: str,
    action: str,
    *,
    fault_scope: bool = True,
    force_error_bucket: bool = False,
):
    tokens = set_correlation_context(
        session_id if fault_scope else f"setup-{session_id}",
        _request_id(session_id, action, force_error_bucket=force_error_bucket),
        str(uuid4()),
    )
    try:
        yield
    finally:
        reset_correlation_context(tokens)


def public_session(session: dict[str, Any]) -> dict[str, Any]:
    """Return the UI contract without protected fault-control internals or secrets."""
    result = json.loads(json.dumps(session))
    result.pop("created_by", None)
    if result.get("approval"):
        result["approval"].pop("approved_by", None)
    snapshots = result.get("snapshots", {})
    if "assistant" in snapshots:
        snapshots["assistant"].pop("evidence_index", None)
    challenge = result.get("challenge")
    if isinstance(challenge, dict):
        challenge.pop("secret_nonce", None)
        if challenge.get("revealed_at") is None:
            result.pop("activation", None)
            if result.get("state") == "FAULT_ARMED":
                result["state"] = "RUNNING"
            result["scenario"] = {
                "scenario_id": "SEALED_RANDOM_CHALLENGE",
                "title": "Blind random challenge",
                "summary": (
                    "The backend committed one verified scenario. Its identity remains sealed "
                    "until evidence analysis is complete."
                ),
                "fault_id": None,
                "target": None,
                "intensity": None,
                "expected_step": None,
                "expected_funds_status": "SEALED",
                "recommended": True,
            }
    return result


def build_blind_comparison(session: dict[str, Any]) -> dict[str, Any]:
    if session.get("mode") != "BLIND_RANDOM" or not session.get("challenge"):
        raise ValueError("session is not a blind random challenge")
    scenario = session["scenario"]
    challenge = session["challenge"]
    if not verify_blind_commitment(challenge, scenario["scenario_id"]):
        raise RuntimeError("blind challenge commitment verification failed")

    intelligence = (session.get("snapshots") or {}).get("intelligence") or {}
    model = intelligence.get("model") or {}
    model_result = model.get("result") or {}
    classification = model_result.get("classification") or {}
    predicted_label = classification.get("label")
    expected_label = scenario.get("fault_id") or "NORMAL"
    origin = intelligence.get("probable_origin") or {}
    facts = intelligence.get("operational_facts") or {}
    observed_step = origin.get("step") or facts.get("observed_step")
    expected_step = scenario.get("expected_step")
    return {
        "commitment_verified": True,
        "selector_version": challenge["selector_version"],
        "injected_scenario": {
            "scenario_id": scenario["scenario_id"],
            "fault_id": scenario.get("fault_id"),
            "target": scenario.get("target"),
            "expected_step": expected_step,
        },
        "model_comparison": {
            "status": model.get("status", "UNAVAILABLE"),
            "predicted_label": predicted_label,
            "expected_label": expected_label,
            "match": predicted_label == expected_label if predicted_label else None,
            "ground_truth_used_by_model": False,
        },
        "workflow_comparison": {
            "observed_service": origin.get("service") or facts.get("observed_service"),
            "observed_step": observed_step,
            "expected_step": expected_step,
            "step_match": (
                observed_step == expected_step
                if expected_step is not None
                else not bool(facts.get("failure_observed"))
            ),
            "basis": "persisted workflow evidence collected before reveal",
        },
    }
