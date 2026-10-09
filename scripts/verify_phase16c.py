"""Verify evidence-first transfer intelligence against accepted Phase 16B cases."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = ROOT / "data" / "experiments" / "transfer-fault-cases"
MODEL_STATUSES = {"EVALUATED", "WINDOW_NOT_READY", "WINDOW_NOT_CORRELATED"}


def latest_acceptance() -> dict:
    paths = sorted(CASE_ROOT.glob("acceptance-*.json"), key=lambda path: path.stat().st_mtime)
    if not paths:
        raise RuntimeError("no Phase 16B acceptance summary is available")
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    if payload.get("case_count") != 5 or payload.get("recovery_verified") is not True:
        raise RuntimeError("latest Phase 16B acceptance summary is incomplete")
    return payload


def main() -> int:
    load_dotenv(ROOT / ".env")
    gateway = os.getenv("GATEWAY_BASE_URL", "http://localhost:18110").rstrip("/")
    username = os.getenv("DEMO_USERNAME", "demo")
    password = os.getenv("DEMO_PASSWORD", "faultweave-demo")
    acceptance = latest_acceptance()
    with httpx.Client(base_url=gateway, timeout=30.0) as client:
        ready = client.get("/api/v1/intelligence/ready")
        ready.raise_for_status()
        if ready.json().get("status") != "ready":
            raise RuntimeError("frozen model artifacts are not ready")
        login = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        login.raise_for_status()
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

        statuses: dict[str, str] = {}
        for case in acceptance["cases"]:
            response = client.get(f"/api/v1/transfers/{case['transfer_id']}/intelligence")
            response.raise_for_status()
            report = response.json()
            facts = report.get("operational_facts") or {}
            origin = report.get("probable_origin") or {}
            authority = report.get("decision_authority") or {}
            model = report.get("model") or {}
            if report.get("transfer_state") != "COMPLETED":
                raise RuntimeError(f"{case['case_id']}: transfer is not completed")
            if facts.get("failure_observed") is not True:
                raise RuntimeError(f"{case['case_id']}: failure evidence is missing")
            if facts.get("recovered_after_review") is not True:
                raise RuntimeError(f"{case['case_id']}: reviewed recovery is missing")
            if origin.get("status") != "OBSERVED_IN_WORKFLOW_EVIDENCE":
                raise RuntimeError(f"{case['case_id']}: observed origin is missing")
            if authority.get("ground_truth_used") is not False:
                raise RuntimeError(f"{case['case_id']}: ground-truth isolation failed")
            if model.get("status") not in MODEL_STATUSES:
                raise RuntimeError(f"{case['case_id']}: invalid model status")
            if model["status"] == "EVALUATED" and not isinstance(model.get("result"), dict):
                raise RuntimeError(f"{case['case_id']}: evaluated model result is missing")
            if model["status"] != "EVALUATED" and model.get("result") is not None:
                raise RuntimeError(f"{case['case_id']}: unvalidated model result was attached")
            statuses[str(case["case_id"])] = str(model["status"])

    print("PASS: Phase 16C evidence-first transfer intelligence verified")
    print("Frozen model artifacts: ready")
    for case_id, status in statuses.items():
        print(f"{case_id}: operational evidence verified; model={status}")
    print("No protected fault ground truth was used by the transfer-intelligence API.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: Phase 16C verification failed: {exc}")
        raise SystemExit(1) from exc
