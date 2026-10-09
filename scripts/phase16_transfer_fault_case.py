"""Create and recover one evidence-backed transfer fault case."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from experiments.faults.adapters import (
    activate_infrastructure_fault,
    deactivate_infrastructure_fault,
    verify_stack_recovery,
)
from experiments.faults.lifecycle import FaultLease, FaultStateStore
from experiments.manifest import sha256_file, write_json
from experiments.transfer_fault_cases import TRANSFER_FAULT_CASES, get_transfer_fault_case

ROOT = Path(__file__).resolve().parents[1]
CONTROL_DIRECTORY = ROOT / "data" / "fault-control"
OUTPUT_DIRECTORY = ROOT / "data" / "experiments" / "transfer-fault-cases"


def checked(response: httpx.Response) -> dict:
    response.raise_for_status()
    return response.json()


def client_for_run(run_id: str, request_id: str, *, actor: str = "customer") -> httpx.Client:
    base = os.getenv(
        "GATEWAY_BASE_URL", f"http://localhost:{os.getenv('GATEWAY_HOST_PORT', '18110')}"
    )
    client = httpx.Client(base_url=base, timeout=70.0)
    if actor == "engineer":
        username = os.getenv("ENGINEER_USERNAME", "engineer")
        password = os.getenv("ENGINEER_PASSWORD", "faultweave-engineer")
    else:
        username = os.getenv("DEMO_USERNAME", "demo")
        password = os.getenv("DEMO_PASSWORD", "faultweave-demo")
    login = checked(
        client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
    )
    if login.get("role") != actor:
        raise RuntimeError(f"expected {actor} credentials, received {login.get('role')}")
    client.headers.update(
        {
            "Authorization": f"Bearer {login['access_token']}",
            "X-Run-ID": run_id,
            "X-Request-ID": request_id,
            "X-Trace-ID": str(uuid4()),
        }
    )
    return client


def request_id_for(run_id: str, requires_injected_error: bool) -> str:
    for sequence in range(1, 10_000):
        candidate = f"{run_id}-request-{sequence:04d}"
        bucket = int(hashlib.sha256(candidate.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if not requires_injected_error or bucket < 0.9:
            return candidate
    raise RuntimeError("unable to choose a deterministic controlled-error request")


def case_directory(run_id: str) -> Path:
    return OUTPUT_DIRECTORY / run_id


def start_case(case_id: str) -> Path:
    definition = get_transfer_fault_case(case_id)
    run_id = f"phase16b-{uuid4().hex[:12]}"
    request_id = request_id_for(run_id, case_id == "account-error-response")
    suffix = run_id.rsplit("-", 1)[-1].upper()
    destination_number = f"FW-FAULT-{suffix}"
    transfer_key = f"fault-case-{run_id}"
    client = client_for_run(run_id, request_id)
    try:
        source_number = os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001")
        sender_before = checked(client.get(f"/api/v1/accounts/{source_number}"))
        receiver_before = checked(
            client.post("/api/v1/accounts", json={"account_number": destination_number})
        )
        transfer_payload = {
            "source_account_number": source_number,
            "destination_account_number": destination_number,
            "amount_minor": 1000,
            "currency": "INR",
            "idempotency_key": transfer_key,
        }
        store = FaultStateStore(CONTROL_DIRECTORY)
        activation = store.acquire(
            run_id=run_id,
            fault_id=definition.fault_id,
            target=definition.target,
            intensity=definition.intensity,
            duration_seconds=45,
        )
        with FaultLease(
            store,
            activation,
            activate_infrastructure_fault,
            deactivate_infrastructure_fault,
            verify_stack_recovery,
        ):
            started = checked(client.post("/api/v1/transfers", json=transfer_payload))
        evidence = checked(client.get(f"/api/v1/transfers/{started['id']}"))
        if evidence["state"] != "REQUIRES_REVIEW":
            raise RuntimeError(f"fault case did not pause for review: {evidence['state']}")
        observed_step = (evidence.get("last_error") or {}).get("step")
        if observed_step != definition.expected_step:
            raise RuntimeError(
                f"expected failure step {definition.expected_step}, observed {observed_step}"
            )
        if evidence["funds_status"] != definition.expected_funds_status:
            raise RuntimeError(
                "expected funds status "
                f"{definition.expected_funds_status}, observed {evidence['funds_status']}"
            )
        output = case_directory(run_id)
        output.mkdir(parents=True, exist_ok=False)
        observable_path = output / "observable-case.json"
        truth_path = output / "ground-truth.json"
        write_json(
            observable_path,
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "transfer_id": started["id"],
                "status": "AWAITING_ENGINEER_REVIEW",
                "transfer": transfer_payload,
                "sender_before": sender_before,
                "receiver_before": receiver_before,
                "case_evidence": evidence,
            },
        )
        write_json(
            truth_path,
            {
                "schema_version": "1.0",
                "protected": True,
                "excluded_from_inference": True,
                "case": definition.model_dump(mode="json"),
                "activation": activation.model_dump(mode="json"),
            },
        )
        manifest_path = output / "manifest.json"
        write_json(
            manifest_path,
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "transfer_id": started["id"],
                "case_id": case_id,
                "status": "AWAITING_ENGINEER_REVIEW",
                "created_at": datetime.now(UTC).isoformat(),
                "observable_case": {
                    "path": str(observable_path.relative_to(ROOT)),
                    "sha256": sha256_file(observable_path),
                },
                "ground_truth": {
                    "path": str(truth_path.relative_to(ROOT)),
                    "sha256": sha256_file(truth_path),
                    "inference_eligible": False,
                },
                "fault_cleaned_up": True,
                "recovery_verified": False,
            },
        )
        return manifest_path
    finally:
        client.close()


def load_manifest(run_id: str) -> tuple[Path, dict]:
    path = case_directory(run_id) / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"case manifest is unavailable: {path}") from exc
    truth_path = ROOT / manifest["ground_truth"]["path"]
    observable_path = ROOT / manifest["observable_case"]["path"]
    if sha256_file(truth_path) != manifest["ground_truth"]["sha256"]:
        raise RuntimeError("protected ground truth checksum changed")
    if sha256_file(observable_path) != manifest["observable_case"]["sha256"]:
        raise RuntimeError("observable case checksum changed")
    return path, manifest


def resume_case(run_id: str, note: str) -> Path:
    manifest_path, manifest = load_manifest(run_id)
    if manifest["status"] != "AWAITING_ENGINEER_REVIEW":
        raise RuntimeError(f"case cannot be resumed from state {manifest['status']}")
    request_id = f"{run_id}-review-{uuid4().hex[:8]}"
    client = client_for_run(run_id, request_id, actor="engineer")
    customer_client: httpx.Client | None = None
    try:
        transfer_id = manifest["transfer_id"]
        before = checked(client.get(f"/api/v1/transfers/{transfer_id}"))
        if before["state"] != "REQUIRES_REVIEW":
            raise RuntimeError(f"database case is not awaiting review: {before['state']}")
        completed = checked(
            client.post(f"/api/v1/transfers/{transfer_id}/resume", json={"note": note})
        )
        if completed["state"] != "COMPLETED":
            raise RuntimeError(f"reviewed transfer did not complete: {completed['state']}")
        evidence = checked(client.get(f"/api/v1/transfers/{transfer_id}"))
        if evidence["state"] != "COMPLETED" or evidence["funds_status"] != "COMMITTED":
            raise RuntimeError("recovery lacks completed funds confirmation")
        history = evidence.get("case_history", {})
        if (
            not history.get("failures")
            or not history.get("reviews")
            or not history.get("recoveries")
        ):
            raise RuntimeError("failure, review, and recovery history is incomplete")
        entries = evidence["payment"]["evidence"]["ledger_posting"]["evidence"]["entries"]
        if len(entries) != 2 or {item["direction"] for item in entries} != {
            "DEBIT",
            "CREDIT",
        }:
            raise RuntimeError("recovered transfer has no balanced journal pair")
        observable_path = ROOT / manifest["observable_case"]["path"]
        observable = json.loads(observable_path.read_text(encoding="utf-8"))
        transfer = observable["transfer"]
        customer_client = client_for_run(
            run_id,
            f"{run_id}-balance-{uuid4().hex[:8]}",
            actor="customer",
        )
        sender_after = checked(
            customer_client.get(f"/api/v1/accounts/{transfer['source_account_number']}")
        )
        receiver_after = checked(
            customer_client.get(f"/api/v1/accounts/{transfer['destination_account_number']}")
        )
        if (
            sender_after["balance_minor"]
            != observable["sender_before"]["balance_minor"] - transfer["amount_minor"]
            or receiver_after["balance_minor"] != transfer["amount_minor"]
        ):
            raise RuntimeError("recovered transfer balances do not match one funds movement")
        observable.update(
            {
                "status": "RECOVERY_VERIFIED",
                "recovered_case_evidence": evidence,
                "sender_after": sender_after,
                "receiver_after": receiver_after,
            }
        )
        write_json(observable_path, observable)
        manifest.update(
            {
                "status": "RECOVERY_VERIFIED",
                "reviewed_at": datetime.now(UTC).isoformat(),
                "recovery_verified": True,
            }
        )
        manifest["observable_case"]["sha256"] = sha256_file(observable_path)
        write_json(manifest_path, manifest)
        return manifest_path
    finally:
        if customer_client is not None:
            customer_client.close()
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="inject a fault and leave its transfer for review")
    start.add_argument("--case", choices=tuple(TRANSFER_FAULT_CASES), required=True)
    resume = commands.add_parser("resume", help="record review and recover the same transfer")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--note", required=True)
    commands.add_parser("list", help="show the validated transfer fault cases")
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    try:
        if args.command == "list":
            for item in TRANSFER_FAULT_CASES.values():
                print(f"{item.case_id}: {item.title} [{item.expected_step}]")
            return 0
        if args.command == "start":
            path = start_case(args.case)
            manifest = json.loads(path.read_text(encoding="utf-8"))
            print("PASS: Fault observed, cleaned up, and left for engineer review")
            print(f"Run ID: {manifest['run_id']}")
            print(f"Transfer ID: {manifest['transfer_id']}")
            print(f"Evidence manifest: {path}")
            print(
                "Next: python -m scripts.phase16_transfer_fault_case resume "
                f"--run-id {manifest['run_id']} "
                '--note "Reviewed evidence and restored dependency"'
            )
            return 0
        path = resume_case(args.run_id, args.note.strip())
        print("PASS: Engineer review persisted and the same transfer recovered exactly once")
        print(f"Evidence manifest: {path}")
        return 0
    except (AssertionError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
