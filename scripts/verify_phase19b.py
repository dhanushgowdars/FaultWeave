"""Live acceptance for the model-ready Phase 19B blind challenge."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "data" / "experiments" / "phase19b-acceptance"


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
        raise RuntimeError("Phase 19B acceptance requires engineer credentials")
    client.headers["Authorization"] = f"Bearer {result['access_token']}"


def start_bridge(gateway_url: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "scripts/live_telemetry_bridge.py",
            "--gateway-url",
            gateway_url,
            "--since",
            "5s",
            "--flush-interval",
            "0.25",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop_bridge(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def verify_commitment(session: dict, comparison: dict) -> None:
    challenge = session["challenge"]
    scenario_id = session["scenario"]["scenario_id"]
    payload = (
        f"{challenge['selector_version']}:{challenge['revealed_nonce']}:{scenario_id}"
    ).encode()
    if hashlib.sha256(payload).hexdigest() != challenge["commitment_sha256"]:
        raise RuntimeError("public commit/reveal proof did not verify")
    if comparison.get("commitment_verified") is not True:
        raise RuntimeError("backend did not verify the blind commitment")


def acceptance(gateway: str, username: str, password: str) -> dict:
    destination = f"FW-P19B-{uuid4().hex[:12].upper()}"
    with httpx.Client(base_url=gateway, timeout=240.0) as client:
        login(client, username, password)
        contract = checked(client.get("/api/v1/demo/contract"))
        blind = contract.get("blind_random_challenge") or {}
        if blind.get("sustained_observable_workload") is not True:
            raise RuntimeError("demo contract does not require a sustained workload")
        if blind.get("model_window_required_before_analysis") is not True:
            raise RuntimeError("demo contract permits analysis without a model window")

        prepared = checked(
            client.post(
                "/api/v1/demo/sessions",
                json={
                    "mode": "BLIND_RANDOM",
                    "destination_account_number": destination,
                    "amount_minor": 625,
                    "currency": "INR",
                    "sender_name": "FaultWeave demo sender",
                    "recipient_name": "Blind model challenge recipient",
                    "payment_channel": "UPI",
                    "purpose": "Phase 19B frozen-model acceptance",
                },
            )
        )
        # Require explicit versioned research-path coverage BEFORE running any workload.
        selector = prepared.get("challenge") or {}
        if (selector.get("selector_version") != "phase19b4-research-v1"
                or selector.get("pool_size") != 5):
            raise RuntimeError(
                "Model-ready selector not active in gateway; set "
                "FAULTWEAVE_MODEL_READY_BLIND_POOL=1 in local .env and rebuild gateway. "
                "Only a PREPARED session was created; no workload started."
            )
        session_id = prepared["session_id"]
        if prepared["scenario"]["scenario_id"] != "SEALED_RANDOM_CHALLENGE":
            raise RuntimeError("scenario leaked before sustained execution")

        executed = checked(client.post(f"/api/v1/demo/sessions/{session_id}/run"))
        run_session = executed["session"]
        workload = executed.get("workload") or {}
        if run_session["scenario"]["scenario_id"] != "SEALED_RANDOM_CHALLENGE":
            raise RuntimeError("scenario leaked before model analysis")
        if workload.get("status") != "READY":
            raise RuntimeError(f"model workload is not ready: {workload.get('status')}")
        if workload.get("ground_truth_used") is not False:
            raise RuntimeError("workload used protected scenario ground truth")
        model_window = workload.get("model_window") or {}
        if model_window.get("window_seconds") != 60:
            raise RuntimeError("sustained run did not produce a 60-second window")
        if model_window.get("request_observation_source") != "client_observed":
            raise RuntimeError("model window lacks client-observed request telemetry")
        if int(model_window.get("event_count") or 0) <= 0:
            raise RuntimeError("model window contains no real service events")

        analysis = checked(client.post(f"/api/v1/demo/sessions/{session_id}/analyze"))
        model = analysis["intelligence"]["model"]
        if model.get("status") != "EVALUATED":
            raise RuntimeError(f"frozen model was not evaluated: {model.get('status')}")
        # Do not accept the old blended recovery window as Phase 19B.2 evidence.
        if model.get("scope") != "challenge_only_telemetry":
            raise RuntimeError(
                "Phase 19B.2 requires challenge-only inference; set "
                "FAULTWEAVE_DEMO_CHALLENGE_SECONDS=70 in the gateway configuration"
            )
        challenge_phase = next(
            (p for p in workload.get("phases", []) if p.get("phase") == "challenge"), None
        )
        if not challenge_phase or not challenge_phase.get("ended_at"):
            raise RuntimeError("missing genuine challenge timestamp boundaries")
        window = model.get("window") or {}
        parse = lambda x: datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        if not (
            parse(challenge_phase["started_at"]) <= parse(window["window_started_at"])
            < parse(window["window_ended_at"]) <= parse(challenge_phase["ended_at"])
        ):
            raise RuntimeError("model inference window crossed challenge boundaries")
        if analysis["session"]["scenario"]["scenario_id"] != "SEALED_RANDOM_CHALLENGE":
            raise RuntimeError("analysis leaked the selected scenario")
        if analysis["intelligence"]["decision_authority"].get("ground_truth_used") is not False:
            raise RuntimeError("model used protected ground truth")

        revealed = checked(client.post(f"/api/v1/demo/sessions/{session_id}/reveal"))
        final = revealed["session"]
        comparison = revealed["comparison"]
        verify_commitment(final, comparison)
        scenario = final["scenario"]
        model_result = model.get("result") or {}
        expected_decision = "NORMAL" if scenario.get("fault_id") is None else "ABNORMAL"
        observed_decision = model_result.get("status")
        if observed_decision not in {"NORMAL", "ABNORMAL"}:
            raise RuntimeError(f"frozen model returned invalid decision {observed_decision!r}")
        anomaly_decision_match = observed_decision == expected_decision

        recovered = False
        if final["state"] == "AWAITING_REVIEW":
            checked(
                client.post(
                    f"/api/v1/demo/sessions/{session_id}/approve",
                    json={
                        "note": (
                            "Reviewed the sealed run's persisted evidence and frozen-model "
                            "result; approve idempotent recovery of the same transfer."
                        )
                    },
                )
            )
            recovery = checked(client.post(f"/api/v1/demo/sessions/{session_id}/recover"))
            verification = recovery["session"].get("verification") or {}
            if not verification.get("single_funds_movement"):
                raise RuntimeError("recovery lacks single-funds-movement proof")
            if not verification.get("balanced_journal"):
                raise RuntimeError("recovery lacks balanced-journal proof")
            recovered = True

        return {
            "schema_version": "1.0",
            "verified_at": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "selected_scenario": scenario["scenario_id"],
            "fault_id": scenario.get("fault_id"),
            "execution_state": run_session["state"],
            "workload": workload,
            "model": model,
            "model_match": comparison["model_comparison"]["match"],
            "expected_anomaly_decision": expected_decision,
            "anomaly_decision_match": anomaly_decision_match,
            "commitment_sha256": final["challenge"]["commitment_sha256"],
            "commitment_verified": True,
            "ground_truth_used": False,
            "recovered": recovered,
        }


def main() -> int:
    load_dotenv(ROOT / ".env")
    gateway = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110").rstrip("/")
    username = os.getenv("ENGINEER_USERNAME", "engineer")
    password = os.getenv("ENGINEER_PASSWORD", "faultweave-engineer")
    bridge = start_bridge(gateway)
    try:
        time.sleep(2)
        if bridge.poll() is not None:
            detail = bridge.stderr.read().strip() if bridge.stderr else ""
            raise RuntimeError(f"telemetry bridge stopped early: {detail}")
        result = acceptance(gateway, username, password)
    finally:
        stop_bridge(bridge)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_ROOT / f"acceptance-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    # The selected fault is revealed by acceptance() BEFORE this audit is called.
    from phase19b3_audit import assess_acceptance
    result["phase19b3_diagnostic"] = assess_acceptance(result)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    classification = (result["model"].get("result") or {}).get("classification") or {}
    print("EXECUTED: Phase 19B blind challenge; scientific verdict follows")
    print(f"Session: {result['session_id']}")
    print(f"Selected after reveal: {result['selected_scenario']}")
    print(f"Frozen model: EVALUATED / {(result['model']['result'])['status']}")
    print(f"Predicted class: {classification.get('label')}")
    print(f"Anomaly decision matched revealed type: {result['anomaly_decision_match']}")
    print(f"Exact class match after reveal: {result['model_match']}")
    print("One visible funds movement; background traffic used the isolated research path")
    print("Ground truth remained sealed until after frozen-model analysis")
    print(f"Acceptance summary: {path}")
    verdict = result["phase19b3_diagnostic"]
    print(f"Phase 19B.3 verdict: {verdict['status']} / {verdict['reason']}")
    # Persist evidence first, and never return success for an invalid diagnostic.
    return 0 if verdict["status"] == "ACCEPTED" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: Phase 19B verification failed: {exc}")
        raise SystemExit(1) from exc
