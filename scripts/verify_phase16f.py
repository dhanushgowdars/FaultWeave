"""Live acceptance for the grounded, read-only incident investigation assistant."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "shared"
EXPECTED_PROVIDER = "groq"
EXPECTED_MODEL = "openai/gpt-oss-120b"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

load_dotenv(ROOT / ".env")

from faultweave_common.security import create_access_token  # noqa: E402


def checked(response: httpx.Response) -> dict:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object from {response.request.url}")
    return value


def cited_event_ids(response: dict) -> set[str]:
    analysis = response.get("analysis") or {}
    statements = [
        analysis.get("first_failure"),
        analysis.get("funds_state"),
        analysis.get("model_assessment"),
        analysis.get("probable_cause"),
        *(analysis.get("observed_facts") or []),
        analysis.get("recovery_assessment"),
    ]
    return {
        event_id
        for statement in statements
        if isinstance(statement, dict)
        for event_id in statement.get("evidence_event_ids", [])
    }


def main() -> int:
    gateway = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110").rstrip("/")
    username = os.getenv("DEMO_USERNAME", "demo")
    password = os.getenv("DEMO_PASSWORD", "faultweave-demo")
    suffix = uuid4().hex[:12]
    key = f"phase16f-{suffix}"
    destination = f"FW-ASSIST-{suffix.upper()}"

    with httpx.Client(base_url=gateway, timeout=70.0) as client:
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
                "X-Trace-ID": str(uuid4()),
            }
        )
        contract = checked(client.get("/api/v1/contracts/transfer-intelligence"))
        assistant_contract = contract.get("assistant_contract") or {}
        if assistant_contract.get("contract_version") != "16f.1":
            raise RuntimeError("gateway did not publish the Phase 16F assistant contract")
        if assistant_contract.get("read_only") is not True:
            raise RuntimeError("assistant contract is not read-only")
        if assistant_contract.get("deterministic_fallback") is not True:
            raise RuntimeError("assistant contract has no deterministic fallback")

        source = os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001")
        checked(client.get(f"/api/v1/accounts/{source}"))
        checked(client.post("/api/v1/accounts", json={"account_number": destination}))
        transfer = checked(
            client.post(
                "/api/v1/transfers",
                json={
                    "source_account_number": source,
                    "destination_account_number": destination,
                    "amount_minor": 100,
                    "currency": "INR",
                    "idempotency_key": key,
                },
            )
        )
        if transfer.get("state") != "COMPLETED":
            raise RuntimeError("assistant acceptance transfer did not complete")
        transfer_id = transfer["id"]

        evidence_before = checked(client.get(f"/api/v1/transfers/{transfer_id}/evidence/export"))
        first = checked(
            client.post(
                f"/api/v1/transfers/{transfer_id}/assistant/explain",
                json={"focus": "overview"},
            )
        )
        second = checked(
            client.post(
                f"/api/v1/transfers/{transfer_id}/assistant/explain",
                json={"focus": "overview"},
            )
        )
        evidence_after = checked(client.get(f"/api/v1/transfers/{transfer_id}/evidence/export"))

        if evidence_before != evidence_after:
            raise RuntimeError("assistant request mutated durable transfer evidence")
        if first.get("status") != "AI_GENERATED":
            fallback = first.get("provenance", {}).get("fallback_reason")
            raise RuntimeError(
                "strict live gate required AI_GENERATED, "
                f"observed {first.get('status')}; fallback_reason={fallback}"
            )
        provenance = first.get("provenance") or {}
        if provenance.get("provider") != EXPECTED_PROVIDER:
            raise RuntimeError(
                f"expected provider {EXPECTED_PROVIDER}, observed {provenance.get('provider')}"
            )
        if provenance.get("model") != EXPECTED_MODEL:
            raise RuntimeError(
                f"expected model {EXPECTED_MODEL}, observed {provenance.get('model')}"
            )
        if first.get("safety", {}).get("read_only") is not True:
            raise RuntimeError("assistant response does not declare read-only behavior")
        if first.get("safety", {}).get("actions_performed") != []:
            raise RuntimeError("assistant claimed that it performed an action")
        if first.get("provenance", {}).get("ground_truth_used") is not False:
            raise RuntimeError("assistant provenance did not exclude protected ground truth")
        if second.get("provenance", {}).get("cached") is not True:
            raise RuntimeError("identical assistant request did not use the bounded cache")

        evidence_ids = set(first.get("evidence_index") or {})
        unknown_citations = cited_event_ids(first) - evidence_ids
        if unknown_citations:
            raise RuntimeError(
                "assistant cited unavailable event IDs: " + ", ".join(sorted(unknown_citations))
            )
        serialized = json.dumps(first, sort_keys=True)
        forbidden = [source, destination, key, login["access_token"]]
        if any(value in serialized for value in forbidden):
            raise RuntimeError("assistant response exposed a protected identifier")

        outsider = create_access_token(str(uuid4()), role="customer")
        outsider_response = client.post(
            f"/api/v1/transfers/{transfer_id}/assistant/explain",
            headers={"Authorization": f"Bearer {outsider}"},
            json={"focus": "overview"},
        )
        if outsider_response.status_code != 404:
            raise RuntimeError(
                "cross-customer assistant check expected HTTP 404, "
                f"observed HTTP {outsider_response.status_code}"
            )

    output = (
        ROOT
        / "data"
        / "experiments"
        / "phase16f-acceptance"
        / f"acceptance-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "verified_at": datetime.now(UTC).isoformat(),
                "transfer_id": transfer_id,
                "assistant_status": first["status"],
                "provider": first["provenance"]["provider"],
                "model": first["provenance"]["model"],
                "evidence_sha256": first["provenance"]["evidence_sha256"],
                "citations_verified": True,
                "authorization_verified": True,
                "read_only_verified": True,
                "cache_verified": True,
                "real_provider_verified": True,
                "strict_schema_verified": True,
                "ground_truth_used": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print("PASS: Phase 16F grounded incident assistant verified")
    print(f"Transfer: {transfer_id}")
    print(f"Mode: {first['status']}")
    print(f"Provider: {first['provenance']['provider']} / {first['provenance']['model']}")
    print(f"Evidence SHA256: {first['provenance']['evidence_sha256']}")
    print("Real Groq generation and strict structured-output contract: verified")
    print("Authorization, citations, redaction, cache and read-only behavior: verified")
    print(f"Acceptance summary: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: Phase 16F verification failed: {exc}")
        raise SystemExit(1) from exc
