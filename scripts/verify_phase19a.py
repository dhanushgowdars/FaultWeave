"""Live acceptance for the sealed Phase 19A blind random challenge."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "data" / "experiments" / "phase19a-acceptance"


def checked(response: httpx.Response) -> dict:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object from {response.request.url}")
    return value


def login(client: httpx.Client, username: str, password: str) -> None:
    result = checked(
        client.post("/api/v1/auth/login", json={"username": username, "password": password})
    )
    if result.get("role") != "engineer":
        raise RuntimeError("Phase 19A acceptance requires engineer credentials")
    client.headers["Authorization"] = f"Bearer {result['access_token']}"


def verify_commitment(session: dict, comparison: dict) -> None:
    challenge = session["challenge"]
    scenario_id = session["scenario"]["scenario_id"]
    payload = (
        f"{challenge['selector_version']}:{challenge['revealed_nonce']}:{scenario_id}"
    ).encode()
    calculated = hashlib.sha256(payload).hexdigest()
    if calculated != challenge["commitment_sha256"]:
        raise RuntimeError("public commit/reveal proof did not verify")
    if comparison.get("commitment_verified") is not True:
        raise RuntimeError("backend did not verify the blind challenge commitment")


def main() -> int:
    load_dotenv(ROOT / ".env")
    gateway = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110").rstrip("/")
    username = os.getenv("ENGINEER_USERNAME", "engineer")
    password = os.getenv("ENGINEER_PASSWORD", "faultweave-engineer")
    destination = f"FW-P19A-{uuid4().hex[:12].upper()}"

    with httpx.Client(base_url=gateway, timeout=180.0) as client:
        login(client, username, password)
        contract = checked(client.get("/api/v1/demo/contract"))
        blind_contract = contract.get("blind_random_challenge") or {}
        if blind_contract.get("enabled") is not True:
            raise RuntimeError("blind random challenge is not enabled")
        if contract.get("ground_truth_used_by_model_or_assistant") is not False:
            raise RuntimeError("demo contract permits ground-truth leakage")

        prepared = checked(
            client.post(
                "/api/v1/demo/sessions",
                json={
                    "mode": "BLIND_RANDOM",
                    "destination_account_number": destination,
                    "amount_minor": 375,
                    "currency": "INR",
                    "sender_name": "FaultWeave demo sender",
                    "recipient_name": "Blind challenge recipient",
                    "payment_channel": "UPI",
                    "purpose": "Phase 19A blind random acceptance",
                },
            )
        )
        session_id = prepared["session_id"]
        if prepared["scenario"]["scenario_id"] != "SEALED_RANDOM_CHALLENGE":
            raise RuntimeError("scenario leaked while the challenge was prepared")
        if "secret_nonce" in prepared.get("challenge", {}):
            raise RuntimeError("challenge nonce leaked before reveal")

        if client.post(f"/api/v1/demo/sessions/{session_id}/arm").status_code != 409:
            raise RuntimeError("blind challenge allowed direct fault arming")
        if client.post(f"/api/v1/demo/sessions/{session_id}/execute").status_code != 409:
            raise RuntimeError("blind challenge allowed direct execution")

        executed = checked(client.post(f"/api/v1/demo/sessions/{session_id}/run"))
        run_session = executed["session"]
        if run_session["scenario"]["scenario_id"] != "SEALED_RANDOM_CHALLENGE":
            raise RuntimeError("scenario leaked before investigation")
        if "activation" in run_session:
            raise RuntimeError("fault activation metadata leaked before reveal")
        if run_session["state"] not in {"COMPLETED", "AWAITING_REVIEW"}:
            raise RuntimeError("blind challenge did not reach an evidence-bearing state")
        if client.post(f"/api/v1/demo/sessions/{session_id}/reveal").status_code != 409:
            raise RuntimeError("challenge could be revealed before evidence analysis")

        analysis = checked(client.post(f"/api/v1/demo/sessions/{session_id}/analyze"))
        if analysis["session"]["scenario"]["scenario_id"] != "SEALED_RANDOM_CHALLENGE":
            raise RuntimeError("analysis response leaked the protected scenario")
        if analysis["intelligence"]["decision_authority"].get("ground_truth_used") is not False:
            raise RuntimeError("model intelligence used protected ground truth")
        if analysis["assistant"]["provenance"].get("ground_truth_used") is not False:
            raise RuntimeError("assistant used protected ground truth")

        revealed = checked(client.post(f"/api/v1/demo/sessions/{session_id}/reveal"))
        final = revealed["session"]
        comparison = revealed["comparison"]
        verify_commitment(final, comparison)
        if comparison["model_comparison"].get("ground_truth_used_by_model") is not False:
            raise RuntimeError("comparison does not preserve inference independence")

        recovery = None
        if final["state"] == "AWAITING_REVIEW":
            approved = checked(
                client.post(
                    f"/api/v1/demo/sessions/{session_id}/approve",
                    json={
                        "note": (
                            "Reviewed blind-challenge evidence after reveal and approve the "
                            "same-transfer idempotent recovery."
                        )
                    },
                )
            )
            if approved["state"] != "APPROVED":
                raise RuntimeError("engineer approval was not persisted")
            recovery = checked(client.post(f"/api/v1/demo/sessions/{session_id}/recover"))
            verification = recovery["session"].get("verification") or {}
            if not verification.get("single_funds_movement"):
                raise RuntimeError("recovered challenge lacks single-movement proof")
            if not verification.get("balanced_journal"):
                raise RuntimeError("recovered challenge lacks balanced-journal proof")

        if client.get("/health/ready").status_code != 200:
            raise RuntimeError("gateway was not healthy after blind challenge cleanup")

    result = {
        "schema_version": "1.0",
        "verified_at": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "selected_scenario": final["scenario"]["scenario_id"],
        "execution_state": run_session["state"],
        "assistant_status": analysis["assistant"]["status"],
        "model_status": comparison["model_comparison"]["status"],
        "model_match": comparison["model_comparison"]["match"],
        "commitment_sha256": final["challenge"]["commitment_sha256"],
        "commitment_verified": comparison["commitment_verified"],
        "recovered": recovery is not None,
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_ROOT / f"acceptance-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("PASS: Phase 19A sealed blind random challenge verified")
    print(f"Session: {session_id}")
    print(f"Selected after reveal: {result['selected_scenario']}")
    print(f"Execution state: {result['execution_state']}")
    print(f"Model: {result['model_status']} / match={result['model_match']}")
    print("Ground truth remained sealed through execution and analysis")
    print("Commit/reveal, cleanup and human-gated recovery: verified")
    print(f"Acceptance summary: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
