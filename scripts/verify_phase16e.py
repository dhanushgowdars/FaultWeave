"""Live acceptance for stable transfer APIs, evidence export and restart persistence."""

from __future__ import annotations

import argparse
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
SHARED = ROOT / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

# Security configuration is read when faultweave_common.security is imported.
# Load the repository environment first so the locally generated negative-test
# token uses the exact same signing secret as the running services.
load_dotenv(ROOT / ".env")

from faultweave_common.security import create_access_token  # noqa: E402


def checked(response: httpx.Response) -> dict:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object from {response.request.url}")
    return value


def wait_for_transaction_service(url: str, timeout_seconds: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not contacted"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=3.0)
            if response.status_code == 200 and response.json().get("status") == "up":
                return
            last_error = f"HTTP {response.status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(1.0)
    raise RuntimeError(f"transaction service did not recover after restart: {last_error}")


def restart_transaction_service() -> None:
    result = subprocess.run(
        ["docker", "compose", "restart", "transaction-service"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("docker compose could not restart transaction-service")
    port = os.getenv("TRANSACTION_HOST_PORT", "18112")
    wait_for_transaction_service(f"http://localhost:{port}/health/ready")


def validate_timeline(evidence: dict) -> None:
    timeline = evidence.get("timeline") or []
    if not timeline:
        raise RuntimeError("normalized evidence has no timeline")
    timestamps = [
        datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")) for item in timeline
    ]
    if timestamps != sorted(timestamps):
        raise RuntimeError("normalized evidence timeline is not chronological")
    if not all(item.get("request_id") and item.get("trace_id") for item in timeline):
        raise RuntimeError("normalized evidence lost request or trace correlation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-restart",
        action="store_true",
        help="Skip only the live container-restart gate (not for final acceptance).",
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    gateway = os.getenv("GATEWAY_BASE_URL", "http://localhost:18110").rstrip("/")
    username = os.getenv("DEMO_USERNAME", "demo")
    password = os.getenv("DEMO_PASSWORD", "faultweave-demo")
    suffix = uuid4().hex[:12]
    key = f"phase16e-{suffix}"
    trace_id = str(uuid4())
    destination = f"FW-EVIDENCE-{suffix.upper()}"

    with httpx.Client(base_url=gateway, timeout=65.0) as client:
        login = checked(
            client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": password},
            )
        )
        client.headers.update(
            {
                "Authorization": f"Bearer {login['access_token']}",
                "X-Run-ID": key,
                "X-Request-ID": key,
                "X-Trace-ID": trace_id,
            }
        )
        contract = checked(client.get("/api/v1/contracts/transfer-intelligence"))
        model_contract = contract.get("model_contract") or {}
        if contract.get("contract_version") != "16e.1":
            raise RuntimeError("gateway did not publish the Phase 16E contract")
        if model_contract.get("frozen") is not True or model_contract.get("feature_count") != 65:
            raise RuntimeError("frozen 65-feature model contract is not ready")

        source_number = os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001")
        checked(client.get(f"/api/v1/accounts/{source_number}"))
        checked(client.post("/api/v1/accounts", json={"account_number": destination}))
        transfer = checked(
            client.post(
                "/api/v1/transfers",
                json={
                    "source_account_number": source_number,
                    "destination_account_number": destination,
                    "amount_minor": 100,
                    "currency": "INR",
                    "idempotency_key": key,
                },
            )
        )
        if transfer.get("state") != "COMPLETED":
            raise RuntimeError("Phase 16E acceptance transfer did not complete")
        transfer_id = transfer["id"]

        history = checked(client.get("/api/v1/transfers", params={"limit": 10, "offset": 0}))
        if transfer_id not in {item["id"] for item in history.get("items", [])}:
            raise RuntimeError("new transfer is missing from customer history")
        search = checked(
            client.get(
                "/api/v1/transfers/search",
                params={"field": "trace_id", "value": trace_id},
            )
        )
        if transfer_id not in {item["id"] for item in search.get("items", [])}:
            raise RuntimeError("exact trace search did not return the transfer")

        evidence = checked(client.get(f"/api/v1/transfers/{transfer_id}/evidence"))
        validate_timeline(evidence)
        if evidence.get("funds_status") != "COMMITTED" or evidence.get("journal") is None:
            raise RuntimeError("normalized evidence has no committed balanced journal")
        before = checked(client.get(f"/api/v1/transfers/{transfer_id}/evidence/export"))
        before_hash = before.get("integrity", {}).get("canonical_sha256")
        if not isinstance(before_hash, str) or len(before_hash) != 64:
            raise RuntimeError("evidence export checksum is invalid")
        serialized = json.dumps(before, sort_keys=True)
        if "Bearer " in serialized or login["access_token"] in serialized:
            raise RuntimeError("evidence export leaked an access token")

        if client.get("/api/v1/engineer/review-queue").status_code != 403:
            raise RuntimeError("customer accessed the engineer review queue")
        if client.get("/api/v1/transfers", params={"state": "INVALID"}).status_code != 422:
            raise RuntimeError("invalid history state was accepted")
        outsider = create_access_token(str(uuid4()), role="customer")
        outsider_response = client.get(
            f"/api/v1/transfers/{transfer_id}/evidence",
            headers={"Authorization": f"Bearer {outsider}"},
        )
        if outsider_response.status_code != 404:
            raise RuntimeError(
                "cross-customer evidence check expected HTTP 404, "
                f"observed HTTP {outsider_response.status_code}"
            )

        if not args.skip_restart:
            restart_transaction_service()
        after = checked(client.get(f"/api/v1/transfers/{transfer_id}/evidence/export"))
        after_hash = after.get("integrity", {}).get("canonical_sha256")
        if after_hash != before_hash or after != before:
            raise RuntimeError("evidence export changed after transaction-service restart")

        engineer_login = checked(
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": os.getenv("ENGINEER_USERNAME", "engineer"),
                    "password": os.getenv("ENGINEER_PASSWORD", "faultweave-engineer"),
                },
            )
        )
        engineer_queue = checked(
            client.get(
                "/api/v1/engineer/review-queue",
                headers={"Authorization": f"Bearer {engineer_login['access_token']}"},
            )
        )
        if engineer_queue.get("scope") != "engineer":
            raise RuntimeError("engineer review queue contract is invalid")

    output = (
        ROOT
        / "data"
        / "experiments"
        / "phase16e-acceptance"
        / f"acceptance-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "verified_at": datetime.now(UTC).isoformat(),
                "transfer_id": transfer_id,
                "trace_id": trace_id,
                "evidence_sha256": before_hash,
                "restart_verified": not args.skip_restart,
                "authorization_verified": True,
                "model_contract": model_contract,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print("PASS: Phase 16E API and evidence contract verified")
    print(f"Transfer: {transfer_id}")
    print(f"Evidence SHA256: {before_hash}")
    print(f"Restart persistence: {'skipped' if args.skip_restart else 'verified'}")
    print("Authorization: customer ownership and engineer queue separation verified")
    print(f"Acceptance summary: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: Phase 16E verification failed: {exc}")
        raise SystemExit(1) from exc
