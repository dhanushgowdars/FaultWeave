"""Exercise all transfer services and save their actual case evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv


def checked(response: httpx.Response) -> dict:
    response.raise_for_status()
    return response.json()


def main() -> None:
    load_dotenv()
    base = os.getenv(
        "GATEWAY_BASE_URL", f"http://localhost:{os.getenv('GATEWAY_HOST_PORT', '18110')}"
    )
    suffix = uuid4().hex[:12]
    destination_number = f"FW-RECEIVER-{suffix.upper()}"
    key = f"transfer-smoke-{suffix}"
    with httpx.Client(base_url=base, timeout=65.0) as client:
        login = checked(
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": os.getenv("DEMO_USERNAME", "demo"),
                    "password": os.getenv("DEMO_PASSWORD", "faultweave-demo"),
                },
            )
        )
        client.headers.update(
            {
                "Authorization": f"Bearer {login['access_token']}",
                "X-Run-ID": key,
                "X-Trace-ID": str(uuid4()),
                "X-Request-ID": key,
            }
        )
        source_number = os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001")
        source = checked(client.get(f"/api/v1/accounts/{source_number}"))
        destination = checked(
            client.post("/api/v1/accounts", json={"account_number": destination_number})
        )
        assert destination["balance_minor"] == 0
        payload = {
            "source_account_number": source_number,
            "destination_account_number": destination_number,
            "amount_minor": 12500,
            "currency": "INR",
            "idempotency_key": key,
        }
        case = checked(client.post("/api/v1/transfers", json=payload))
        assert case["state"] == "COMPLETED", case
        replay = checked(client.post("/api/v1/transfers", json=payload))
        assert replay["id"] == case["id"]
        evidence = checked(client.get(f"/api/v1/transfers/{case['id']}"))
        assert evidence["state"] == "COMPLETED"
        assert evidence["funds_status"] == "COMMITTED"
        journal = evidence["payment"]["evidence"]["ledger_posting"]["evidence"]
        assert len(journal["entries"]) == 2
        assert {entry["direction"] for entry in journal["entries"]} == {"DEBIT", "CREDIT"}
        assert {entry["amount_minor"] for entry in journal["entries"]} == {12500}
        source_after = checked(client.get(f"/api/v1/accounts/{source_number}"))
        receiver_after = checked(client.get(f"/api/v1/accounts/{destination_number}"))
        assert source_after["balance_minor"] == source["balance_minor"] - 12500
        assert receiver_after["balance_minor"] == 12500
        assert (
            client.post("/api/v1/transfers", json={**payload, "amount_minor": 12501}).status_code
            == 409
        )
        rejected = checked(
            client.post(
                "/api/v1/transfers",
                json={
                    **payload,
                    "source_account_number": destination_number,
                    "destination_account_number": source_number,
                    "amount_minor": 12501,
                    "idempotency_key": f"insufficient-{suffix}",
                },
            )
        )
        assert rejected["state"] == "REQUIRES_REVIEW"
        rejection_evidence = checked(client.get(f"/api/v1/transfers/{rejected['id']}"))
        assert rejection_evidence["funds_status"] == "NOT_COMMITTED"
        assert (
            checked(client.get(f"/api/v1/accounts/{source_number}"))["balance_minor"]
            == source_after["balance_minor"]
        )
        assert (
            checked(client.get(f"/api/v1/accounts/{destination_number}"))["balance_minor"] == 12500
        )
        output = Path("data/experiments/transfer-verification") / f"{suffix}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "completed_case": evidence,
                    "rejected_case": rejection_evidence,
                    "sender_before": source,
                    "sender_after": source_after,
                    "receiver_after": receiver_after,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"PASS: transfer {case['id']} completed across transaction/payment/account/ledger")
        print(
            "PASS: balances, paired journal, idempotent retry and insufficient-funds case verified"
        )
        print(f"Evidence: {output.resolve()}")


if __name__ == "__main__":
    main()
