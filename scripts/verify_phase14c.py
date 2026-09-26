from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_DIRECTORY / "data" / "datasets" / "final"
GATEWAY_URL = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110").rstrip("/")


def _first(path: Path, predicate) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            item = json.loads(line)
            if predicate(item):
                return item
    raise ValueError(f"no verification feature row found in {path}")


def _infer(client: httpx.Client, row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "window_seconds": int(row["window_seconds"]),
        "features": row["features"],
    }
    response = client.post(f"{GATEWAY_URL}/api/v1/intelligence/infer", json=payload)
    response.raise_for_status()
    body = response.json()
    if body.get("window_seconds") != payload["window_seconds"]:
        raise ValueError("live inference response window size changed")
    return body


def main() -> int:
    feature_root = DATASET_ROOT / "features-rich-v2"
    sixty = feature_root / "windows-60s.jsonl"
    ten = feature_root / "windows-10s.jsonl"
    try:
        normal = _first(
            sixty,
            lambda item: item.get("split") == "test" and item.get("label") == "NORMAL",
        )
        known = _first(
            sixty,
            lambda item: item.get("split") == "test"
            and item.get("scenario_type") == "known_fault"
            and item.get("label") != "NORMAL",
        )
        temporal = _first(
            ten,
            lambda item: item.get("split") == "test"
            and item.get("scenario_type") == "known_fault"
            and item.get("interval") == "fault",
        )
        with httpx.Client(timeout=30.0) as client:
            ready = client.get(f"{GATEWAY_URL}/api/v1/intelligence/ready")
            ready.raise_for_status()
            normal_result = _infer(client, normal)
            known_result = _infer(client, known)
            temporal_result = _infer(client, temporal)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"FAIL: Phase 14C live inference verification failed\n- {exc}", file=sys.stderr)
        return 1

    print("PASS: Phase 14C frozen inference endpoint verified")
    print(f"60s normal result: {normal_result['status']}")
    print(
        "60s known-fault result: "
        f"{known_result['status']} / {known_result['classification']['label']}"
    )
    print(f"Offline verification label (not sent to API): {known['label']}")
    print(f"10s temporal result: {temporal_result['status']}")
    print("Only window_seconds and the frozen observable feature vector were sent to inference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
