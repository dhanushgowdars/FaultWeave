from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from experiments.runner import build_plan, execute_plan  # noqa: E402
from experiments.traffic_profiles import get_profile  # noqa: E402
from scripts.live_request_telemetry import LiveRequestTelemetryPublisher  # noqa: E402

GATEWAY_URL = "http://localhost:18110"
DURATION_SECONDS = 62.0
SEED = 20260926


async def run_traffic() -> tuple[int, int, int]:
    profile = get_profile("low")
    run_id = f"phase14f2-live-{uuid4().hex[:8]}"
    plan = build_plan(
        profile,
        seed=SEED,
        duration_seconds=DURATION_SECONDS,
        target_rps=profile.target_rps,
    )
    async with LiveRequestTelemetryPublisher(GATEWAY_URL) as publisher:
        results = await execute_plan(
            plan,
            GATEWAY_URL,
            run_id,
            SEED,
            20,
            run_id,
            publisher.observe,
        )
    return len(results), publisher.accepted_total, publisher.duplicate_total


def main() -> int:
    bridge = subprocess.Popen(
        [
            sys.executable,
            "scripts/live_telemetry_bridge.py",
            "--since",
            "1s",
            "--flush-interval",
            "0.1",
        ],
        cwd=PROJECT_DIRECTORY,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1.0)
        request_count, accepted_requests, duplicate_requests = asyncio.run(
            run_traffic()
        )
        time.sleep(1.0)

        with httpx.Client(timeout=20.0) as client:
            features_response = client.get(
                f"{GATEWAY_URL}/api/v1/intelligence/live/features/60"
            )
            features_response.raise_for_status()
            snapshot = features_response.json()
            inference_response = client.get(
                f"{GATEWAY_URL}/api/v1/intelligence/live/infer/60"
            )
            inference_response.raise_for_status()
            result = inference_response.json()["result"]

        if snapshot.get("request_observation_source") != "client_observed":
            raise ValueError("60-second live window is not using client observations")
        if int(snapshot.get("request_count", 0)) <= 0:
            raise ValueError("60-second live window contains no request observations")
        if int(snapshot.get("event_count", 0)) <= 0:
            raise ValueError("60-second live window contains no structured events")
        if accepted_requests <= 0:
            raise ValueError("continuous publisher reported no accepted observations")
        if result.get("status") != "NORMAL":
            raise ValueError(
                f"healthy continuous traffic classified as {result.get('status')}"
            )

        print("PASS: Phase 14F2 continuous client request telemetry verified")
        print(f"Traffic requests: {request_count}")
        print(f"Client observations accepted: {accepted_requests}")
        print(f"Client observation duplicates: {duplicate_requests}")
        print(
            "60s live window: "
            f"requests={snapshot['request_count']} "
            f"events={snapshot['event_count']} "
            f"source={snapshot['request_observation_source']}"
        )
        print(f"60s inference status: {result['status']}")
        print(
            "No fault label, expected origin, dataset split, or injected interval "
            "was supplied to live inference."
        )
        return 0
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: Phase 14F2 verification failed: {exc}", file=sys.stderr)
        return 1
    finally:
        bridge.terminate()
        try:
            bridge.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bridge.kill()
            bridge.wait(timeout=5)
        if bridge.returncode not in {0, -15, 1} and bridge.stderr is not None:
            error = bridge.stderr.read().strip()
            if error:
                print(f"telemetry bridge stderr: {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
