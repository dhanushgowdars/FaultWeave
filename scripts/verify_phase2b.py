from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from scripts.collect_logs import REQUIRED_FIELDS, extract_event
except ModuleNotFoundError:  # Direct execution uses the scripts directory on sys.path.
    from collect_logs import REQUIRED_FIELDS, extract_event

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
EXPECTED_SERVICES = {
    "gateway",
    "authentication",
    "account",
    "transaction",
    "payment",
    "ledger",
}
EXPECTED_EDGES = {
    ("gateway", "authentication"),
    ("gateway", "account"),
    ("gateway", "transaction"),
    ("transaction", "account"),
    ("transaction", "payment"),
    ("transaction", "ledger"),
    ("payment", "ledger"),
}
EXPECTED_BUSINESS_EVENTS = {
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


def send_flow(gateway_url: str, run_id: str, sequence: int) -> tuple[str, str, dict[str, Any]]:
    request_id = f"{run_id}-request-{sequence:03d}"
    trace_id = str(uuid4())
    payload = json.dumps(
        {
            "username": "demo",
            "password": "faultweave-demo",
            "account_number": "FW-DEMO-001",
            "amount_minor": 1000 + sequence,
            "currency": "INR",
            "recipient": f"phase-2b-verifier-{sequence:03d}",
        }
    ).encode()
    request = urllib.request.Request(
        f"{gateway_url}/api/v1/transactions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Run-ID": run_id,
            "X-Request-ID": request_id,
            "X-Trace-ID": trace_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
        if response.headers.get("X-Run-ID") != run_id:
            raise ValueError("gateway response did not preserve run ID")
        if response.headers.get("X-Request-ID") != request_id:
            raise ValueError("gateway response did not preserve request ID")
        if response.headers.get("X-Trace-ID") != trace_id:
            raise ValueError("gateway response did not preserve trace ID")
    if result.get("status") != "COMPLETED" or result.get("request_id") != request_id:
        raise ValueError(f"unexpected response for flow {sequence}: {result}")
    return request_id, trace_id, result


def validate_run(
    events: list[dict[str, Any]],
    run_id: str,
    expected_correlations: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    related = [event for event in events if event.get("run_id") == run_id]
    if not related:
        return ["no structured events found for verification run"]

    for event in related:
        missing = REQUIRED_FIELDS - event.keys()
        if missing:
            errors.append(f"event missing fields: {sorted(missing)}")
        if event.get("schema_version") != "1.1":
            errors.append("event uses a non-frozen schema version")
        request_id = event.get("request_id")
        if request_id in expected_correlations:
            expected_trace = expected_correlations[request_id]
            if event.get("trace_id") != expected_trace:
                errors.append(f"trace mismatch for {request_id}")

    observed_services = {event.get("service") for event in related}
    missing_services = EXPECTED_SERVICES - observed_services
    if missing_services:
        errors.append(f"missing services: {sorted(missing_services)}")

    observed_edges = {
        (event.get("service"), event.get("downstream_service"))
        for event in related
        if event.get("event_type") == "downstream_request_completed"
    }
    missing_edges = EXPECTED_EDGES - observed_edges
    if missing_edges:
        errors.append(f"missing dependency edges: {sorted(missing_edges)}")

    failures = [
        event
        for event in related
        if event.get("level") in {"ERROR", "CRITICAL"} or event.get("success") is False
    ]
    if failures:
        errors.append(f"unexpected failed events: {len(failures)}")

    for request_id, trace_id in expected_correlations.items():
        timeline = [event for event in related if event.get("request_id") == request_id]
        services = {event.get("service") for event in timeline}
        event_types = {event.get("event_type") for event in timeline}
        if services != EXPECTED_SERVICES:
            errors.append(f"incomplete service timeline for {request_id}")
        if not EXPECTED_BUSINESS_EVENTS.issubset(event_types):
            errors.append(f"incomplete business timeline for {request_id}")
        if any(event.get("trace_id") != trace_id for event in timeline):
            errors.append(f"timeline contains mixed trace IDs for {request_id}")

    serialized = json.dumps(related).lower()
    for secret in ("faultweave-demo", "bearer ey", "plain-text"):
        if secret in serialized:
            errors.append(f"secret marker found: {secret}")
    return list(dict.fromkeys(errors))


def docker_events(since: str = "15m") -> list[dict[str, Any]]:
    logs = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "--since", since],
        cwd=PROJECT_DIRECTORY,
        capture_output=True,
        check=False,
        text=True,
    )
    if logs.returncode != 0:
        raise RuntimeError(logs.stderr.strip() or "could not read Docker logs")
    return [event for line in logs.stdout.splitlines() if (event := extract_event(line))]


def run_verification(flow_count: int, workers: int) -> int:
    gateway_url = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110")
    run_id = f"phase-2b-{uuid4().hex[:12]}"
    correlations: dict[str, str] = {}
    try:
        first_request, first_trace, _ = send_flow(gateway_url, run_id, 1)
        correlations[first_request] = first_trace
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(send_flow, gateway_url, run_id, sequence): sequence
                for sequence in range(2, flow_count + 1)
            }
            for future in as_completed(futures):
                request_id, trace_id, _ = future.result()
                correlations[request_id] = trace_id
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"FAIL: normal-flow verification failed: {exc}", file=sys.stderr)
        return 1

    time.sleep(1.0)
    try:
        events = docker_events()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    errors = validate_run(events, run_id, correlations)
    if errors:
        print("FAIL: Phase 2B architecture freeze verification failed", file=sys.stderr)
        for error in errors[:20]:
            print(f"- {error}", file=sys.stderr)
        return 1

    related_count = sum(event.get("run_id") == run_id for event in events)
    print(f"PASS: Phase 2B verified with {flow_count} completed normal flows")
    print(f"Run ID: {run_id}")
    print(f"Correlated events: {related_count}")
    print(f"Services: {', '.join(sorted(EXPECTED_SERVICES))}")
    print(f"Dependency edges: {len(EXPECTED_EDGES)} of {len(EXPECTED_EDGES)}")
    print("Unexpected failures: 0")
    print("Secrets: not present")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the frozen FaultWeave architecture")
    parser.add_argument("--flows", type=int, default=100)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    if args.flows < 1 or args.workers < 1:
        parser.error("--flows and --workers must be positive")
    return args


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(run_verification(arguments.flows, arguments.workers))
