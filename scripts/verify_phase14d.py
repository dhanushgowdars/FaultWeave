from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from experiments.runner import build_plan, execute_plan, wait_for_run_events  # noqa: E402
from experiments.traffic_profiles import get_profile  # noqa: E402
from scripts.live_telemetry_bridge import eligible_event  # noqa: E402

DURATION_SECONDS = 62.0
SEED = 20260926


def _request_observation(item: dict) -> dict:
    return {
        "request_id": str(item["request_id"]),
        "trace_id": str(item["trace_id"]) if item.get("trace_id") is not None else None,
        "started_at": item["started_at"],
        "latency_ms": float(item["latency_ms"]),
        "status_code": item.get("status_code"),
        "expected_outcome": bool(item["expected_outcome"]),
        "transport_error": item.get("transport_error"),
        "scenario": str(item["scenario"]),
    }


def _post_events(client: httpx.Client, gateway_url: str, events: list[dict]) -> int:
    accepted = 0
    endpoint = f"{gateway_url}/api/v1/intelligence/telemetry"
    for index in range(0, len(events), 500):
        response = client.post(endpoint, json={"events": events[index : index + 500]})
        response.raise_for_status()
        accepted += int(response.json()["accepted"])
    return accepted


def _post_requests(client: httpx.Client, gateway_url: str, rows: list[dict]) -> int:
    accepted = 0
    endpoint = f"{gateway_url}/api/v1/intelligence/telemetry"
    observations = [_request_observation(item) for item in rows]
    for index in range(0, len(observations), 500):
        response = client.post(endpoint, json={"requests": observations[index : index + 500]})
        response.raise_for_status()
        accepted += int(response.json()["accepted_requests"])
    return accepted


def main() -> int:
    gateway_url = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110").rstrip("/")
    run_id = f"phase14d-live-{uuid4().hex[:10]}"
    profile = get_profile("low")
    plan = build_plan(
        profile,
        seed=SEED,
        duration_seconds=DURATION_SECONDS,
        target_rps=profile.target_rps,
    )
    print(
        "Running 62 seconds of ordinary low traffic so both 10s and 60s live windows "
        "are real rather than synthesized..."
    )
    results = asyncio.run(
        execute_plan(
            plan,
            gateway_url,
            run_id,
            SEED,
            max_concurrency=20,
            request_namespace=run_id,
        )
    )
    expected_request_ids = {str(item["request_id"]) for item in results}
    events = wait_for_run_events(run_id, expected_request_ids)
    observed_request_ids = {str(item.get("request_id")) for item in events}
    if not expected_request_ids.issubset(observed_request_ids):
        print(
            "FAIL: not all live verification requests appeared in structured logs",
            file=sys.stderr,
        )
        return 1
    eligible = [event for event in events if eligible_event(event)]
    if not eligible:
        print("FAIL: no eligible live telemetry was collected", file=sys.stderr)
        return 1

    try:
        with httpx.Client(timeout=30.0) as client:
            accepted = _post_events(client, gateway_url, eligible)
            accepted_requests = _post_requests(client, gateway_url, results)
            readiness = client.get(f"{gateway_url}/api/v1/intelligence/ready")
            readiness.raise_for_status()
            feature_count = int(readiness.json()["feature_count"])
            feature_results = {}
            inference_results = {}
            for seconds in (10, 60):
                features = client.get(
                    f"{gateway_url}/api/v1/intelligence/live/features/{seconds}"
                )
                features.raise_for_status()
                feature_results[seconds] = features.json()
                if len(feature_results[seconds]["features"]) != feature_count:
                    print(
                        f"FAIL: {seconds}s live feature schema does not match frozen artifacts",
                        file=sys.stderr,
                    )
                    return 1
                inference = client.get(
                    f"{gateway_url}/api/v1/intelligence/live/infer/{seconds}"
                )
                inference.raise_for_status()
                inference_results[seconds] = inference.json()["result"]
    except httpx.HTTPError as exc:
        print(f"FAIL: Phase 14D live telemetry verification failed: {exc}", file=sys.stderr)
        return 1

    print("PASS: Phase 14D live telemetry and rolling feature windows verified")
    print(f"Run ID: {run_id}")
    print(f"Traffic requests: {len(results)}")
    print(f"Structured events collected: {len(events)}")
    print(f"Eligible events accepted: {accepted}")
    print(f"Client request observations accepted: {accepted_requests}")
    print(
        "10s live window: "
        f"requests={feature_results[10]['request_count']} "
        f"events={feature_results[10]['event_count']} "
        f"status={inference_results[10]['status']}"
    )
    print(
        "60s live window: "
        f"requests={feature_results[60]['request_count']} "
        f"events={feature_results[60]['event_count']} "
        f"status={inference_results[60]['status']}"
    )
    if inference_results[60].get("classification"):
        print(
            "60s live classification: "
            f"{inference_results[60]['classification'].get('label')}"
        )
    print(f"60s request observation source: {feature_results[60]['request_observation_source']}")
    print("No fault label, fault interval, expected origin, or dataset split was ingested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
