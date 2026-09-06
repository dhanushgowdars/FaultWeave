from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from scripts.collect_logs import extract_event
except ModuleNotFoundError:  # Direct execution uses the scripts directory on sys.path.
    from collect_logs import extract_event

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
EXPECTED_SERVICES = {
    "gateway",
    "authentication",
    "account",
    "transaction",
    "payment",
    "ledger",
}
EXPECTED_EVENT_TYPES = {
    "transaction_flow_started",
    "authentication_succeeded",
    "account_lookup_completed",
    "account_validation_completed",
    "transaction_created",
    "payment_completed",
    "ledger_entry_created",
    "transaction_completed",
    "transaction_flow_completed",
}


def validate_correlated_events(events: list[dict[str, Any]], request_id: str) -> list[str]:
    errors: list[str] = []
    related = [event for event in events if event.get("request_id") == request_id]
    services = {event.get("service") for event in related}
    event_types = {event.get("event_type") for event in related}

    missing_services = EXPECTED_SERVICES - services
    if missing_services:
        errors.append(f"missing services: {sorted(missing_services)}")
    missing_events = EXPECTED_EVENT_TYPES - event_types
    if missing_events:
        errors.append(f"missing event types: {sorted(missing_events)}")

    timing_events = [
        event
        for event in related
        if event.get("event_type") in {"http_request_completed", "downstream_request_completed"}
    ]
    if not timing_events:
        errors.append("no HTTP timing events")
    for event in timing_events:
        if event.get("latency_ms") is None or event.get("status_code") is None:
            errors.append(f"incomplete timing event from {event.get('service')}")

    serialized = json.dumps(related).lower()
    for secret in ("faultweave-demo", "bearer ey", "plain-text"):
        if secret in serialized:
            errors.append(f"secret marker found: {secret}")
    return errors


def run_verification() -> int:
    request_id = f"phase-2-verify-{uuid4().hex[:12]}"
    gateway_url = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110")
    payload = json.dumps(
        {
            "username": "demo",
            "password": "faultweave-demo",
            "amount_minor": 12500,
            "currency": "INR",
            "recipient": "phase-2-verifier",
        }
    ).encode()
    request = urllib.request.Request(
        f"{gateway_url}/api/v1/transactions",
        data=payload,
        headers={"Content-Type": "application/json", "X-Request-ID": request_id},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"FAIL: verification transaction failed: {exc}", file=sys.stderr)
        return 1
    if result.get("status") != "COMPLETED":
        print(f"FAIL: unexpected transaction response: {result}", file=sys.stderr)
        return 1

    time.sleep(0.25)
    logs = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "--since", "2m"],
        cwd=PROJECT_DIRECTORY,
        capture_output=True,
        check=False,
        text=True,
    )
    if logs.returncode != 0:
        print(f"FAIL: could not read Docker logs: {logs.stderr.strip()}", file=sys.stderr)
        return 1
    events = [event for line in logs.stdout.splitlines() if (event := extract_event(line))]
    errors = validate_correlated_events(events, request_id)
    if errors:
        print("FAIL: Phase 2 structured-log verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    related_count = sum(event.get("request_id") == request_id for event in events)
    print(f"PASS: Phase 2 verified with {related_count} correlated events")
    print(f"Request ID: {request_id}")
    print(f"Services: {', '.join(sorted(EXPECTED_SERVICES))}")
    print("Secrets: not present")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_verification())
