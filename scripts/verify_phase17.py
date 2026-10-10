"""Live acceptance for the persisted, human-gated Phase 17 demo orchestrator."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "data" / "experiments" / "phase17-acceptance"


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
        raise RuntimeError("Phase 17 acceptance requires engineer credentials")
    client.headers["Authorization"] = f"Bearer {result['access_token']}"


def restart_gateway() -> None:
    result = subprocess.run(
        ["docker", "compose", "restart", "gateway"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def wait_for_gateway(client: httpx.Client) -> None:
    for _ in range(45):
        try:
            if client.get("/health/ready", timeout=2.0).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(1)
    raise RuntimeError("gateway did not become ready after restart")


def create_session(client: httpx.Client, scenario_id: str, amount: int) -> dict:
    suffix = uuid4().hex[:12].upper()
    return checked(
        client.post(
            "/api/v1/demo/sessions",
            json={
                "scenario_id": scenario_id,
                "destination_account_number": f"FW-P17-{suffix}",
                "amount_minor": amount,
                "currency": "INR",
            },
        )
    )


def main() -> int:
    load_dotenv(ROOT / ".env")
    gateway = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110").rstrip("/")
    username = os.getenv("ENGINEER_USERNAME", "engineer")
    password = os.getenv("ENGINEER_PASSWORD", "faultweave-engineer")
    with httpx.Client(base_url=gateway, timeout=120.0) as client:
        login(client, username, password)
        contract = checked(client.get("/api/v1/demo/contract"))
        if contract.get("automatic_recovery") is not False:
            raise RuntimeError("demo contract permits automatic recovery")
        scenarios = checked(client.get("/api/v1/demo/scenarios"))["items"]
        if len(scenarios) != 6:
            raise RuntimeError("demo catalog must expose one normal and five fault cases")

        normal = create_session(client, "normal-transfer", 250)
        normal_id = normal["session_id"]
        normal_result = checked(
            client.post(f"/api/v1/demo/sessions/{normal_id}/execute")
        )
        if normal_result["session"]["state"] != "COMPLETED":
            raise RuntimeError("normal demo transfer did not complete")

        fault = create_session(client, "ledger-service-unavailable", 1000)
        session_id = fault["session_id"]
        armed = checked(client.post(f"/api/v1/demo/sessions/{session_id}/arm"))
        if armed["state"] != "FAULT_ARMED":
            raise RuntimeError("primary demo fault did not arm")
        failed = checked(client.post(f"/api/v1/demo/sessions/{session_id}/execute"))
        if failed["session"]["state"] != "AWAITING_REVIEW":
            raise RuntimeError("fault transfer did not remain paused for engineer review")
        if failed["evidence"]["funds_status"] != "COMMITTED":
            raise RuntimeError("ledger outage did not preserve committed-funds evidence")
        blocked = client.post(f"/api/v1/demo/sessions/{session_id}/recover")
        if blocked.status_code != 409:
            raise RuntimeError("recovery was possible without analysis and approval")

        restart_gateway()
        wait_for_gateway(client)
        login(client, username, password)
        restored = checked(client.get(f"/api/v1/demo/sessions/{session_id}"))
        if restored["state"] != "AWAITING_REVIEW":
            raise RuntimeError("demo session did not survive gateway restart")

        analysis = checked(client.post(f"/api/v1/demo/sessions/{session_id}/analyze"))
        assistant = analysis["assistant"]
        if assistant["provenance"].get("ground_truth_used") is not False:
            raise RuntimeError("assistant used protected scenario ground truth")
        if assistant["status"] not in {"AI_GENERATED", "DETERMINISTIC_FALLBACK"}:
            raise RuntimeError("assistant returned an unsupported provenance status")
        approved = checked(
            client.post(
                f"/api/v1/demo/sessions/{session_id}/approve",
                json={
                    "note": (
                        "Reviewed persisted failure, funds and journal evidence; "
                        "approve idempotent resume."
                    )
                },
            )
        )
        if approved["state"] != "APPROVED":
            raise RuntimeError("engineer approval was not persisted")
        recovered = checked(client.post(f"/api/v1/demo/sessions/{session_id}/recover"))
        final = recovered["session"]
        if final["state"] != "RECOVERED":
            raise RuntimeError("approved case did not reach verified recovery")
        verification = final.get("verification") or {}
        if not verification.get("single_funds_movement") or not verification.get(
            "balanced_journal"
        ):
            raise RuntimeError("recovery lacks financial safety proof")
        transfer_id = final["transfer_id"]
        exported = checked(client.get(f"/api/v1/transfers/{transfer_id}/evidence/export"))
        if exported["integrity"]["algorithm"] != "SHA-256":
            raise RuntimeError("evidence export has no SHA-256 integrity contract")

    result = {
        "schema_version": "1.0",
        "verified_at": datetime.now(UTC).isoformat(),
        "contract": contract,
        "normal_session_id": normal_id,
        "fault_session_id": session_id,
        "transfer_id": transfer_id,
        "final_state": final["state"],
        "assistant_status": assistant["status"],
        "evidence_sha256": exported["integrity"]["canonical_sha256"],
        "checks": {
            "normal_transfer": True,
            "real_controlled_fault": True,
            "paused_for_review": True,
            "restart_persistence": True,
            "grounded_analysis": True,
            "explicit_approval": True,
            "same_transfer_recovery": True,
            "single_funds_movement": True,
            "balanced_journal": True,
            "automatic_recovery": False,
        },
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"))
    result["acceptance_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_ROOT / f"acceptance-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("PASS: Phase 17 persisted demo orchestration verified")
    print(f"Normal session: {normal_id} -> COMPLETED")
    print(f"Fault session: {session_id} -> AWAITING_REVIEW -> APPROVED -> RECOVERED")
    print(f"Recovered transfer: {transfer_id}")
    print(f"Assistant mode: {assistant['status']}")
    print("Automatic recovery: disabled")
    print("Single funds movement and balanced journal: verified")
    print(f"Acceptance summary: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: Phase 17 verification failed: {exc}")
        raise SystemExit(1) from exc
