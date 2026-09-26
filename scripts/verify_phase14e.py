from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.final_quality_profile import parse_timestamp  # noqa: E402

TARGET_FAULT = "AUTHENTICATION_FAILURE_BURST"
GATEWAY_URL = "http://localhost:18110"
BATCH_SIZE = 400


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _interval(truth: dict[str, Any], name: str) -> tuple[datetime, datetime]:
    for item in truth.get("intervals", []):
        if item.get("name") == name:
            return parse_timestamp(item["started_at"]), parse_timestamp(item["ended_at"])
    raise ValueError(f"ground truth lacks {name} interval")


def _detected_run_ids() -> set[str]:
    path = DATASET_ROOT / "impact" / "report.json"
    if not path.is_file():
        return set()
    report = _read_json(path)
    return {
        str(item["run_id"])
        for item in report.get("results", [])
        if item.get("detected") is True
    }


def _select_run() -> tuple[dict[str, Any], dict[str, Any]]:
    detected = _detected_run_ids()
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for path in sorted((DATASET_ROOT / "manifests").glob("*.json")):
        manifest = _read_json(path)
        if manifest.get("scenario_type") != "known_fault":
            continue
        truth = _read_json(PROJECT_DIRECTORY / manifest["ground_truth"]["path"])
        if truth.get("fault_id") == TARGET_FAULT:
            candidates.append((manifest, truth))
    if not candidates:
        raise ValueError(f"no accepted {TARGET_FAULT} run was found")
    for manifest, truth in candidates:
        if str(manifest["run_id"]) in detected:
            return manifest, truth
    return candidates[0]


def _shift_event(
    event: dict[str, Any],
    source_start: datetime,
    target_start: datetime,
) -> dict[str, Any]:
    shifted = dict(event)
    timestamp = parse_timestamp(event["timestamp"])
    shifted_at = target_start + (timestamp - source_start)
    shifted["timestamp"] = shifted_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return shifted


def _shift_request(
    row: dict[str, Any],
    source_start: datetime,
    target_start: datetime,
) -> dict[str, Any]:
    started_at = parse_timestamp(row["started_at"])
    shifted_at = target_start + (started_at - source_start)
    return {
        "request_id": str(row["request_id"]),
        "trace_id": str(row["trace_id"]) if row.get("trace_id") is not None else None,
        "started_at": shifted_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "latency_ms": float(row["latency_ms"]),
        "status_code": row.get("status_code"),
        "expected_outcome": bool(row["expected_outcome"]),
        "transport_error": row.get("transport_error"),
        "scenario": str(row["scenario"]),
    }


def _post_events(client: httpx.Client, events: list[dict[str, Any]]) -> int:
    accepted = 0
    for offset in range(0, len(events), BATCH_SIZE):
        response = client.post(
            f"{GATEWAY_URL}/api/v1/intelligence/telemetry",
            json={"events": events[offset : offset + BATCH_SIZE]},
        )
        response.raise_for_status()
        accepted += int(response.json().get("accepted", 0))
    return accepted


def _post_requests(client: httpx.Client, rows: list[dict[str, Any]]) -> int:
    accepted = 0
    for offset in range(0, len(rows), BATCH_SIZE):
        response = client.post(
            f"{GATEWAY_URL}/api/v1/intelligence/telemetry",
            json={"requests": rows[offset : offset + BATCH_SIZE]},
        )
        response.raise_for_status()
        accepted += int(response.json().get("accepted_requests", 0))
    return accepted


def main() -> int:
    try:
        manifest, truth = _select_run()
        baseline_start, baseline_end = _interval(truth, "baseline")
        fault_start, fault_end = _interval(truth, "fault")
        events = _read_jsonl(PROJECT_DIRECTORY / manifest["events"]["path"])
        requests = _read_jsonl(PROJECT_DIRECTORY / manifest["requests"]["path"])
        selected = [
            item
            for item in events
            if baseline_start <= parse_timestamp(item["timestamp"]) <= fault_end
        ]
        if not selected:
            raise ValueError("selected verification run contains no structured events")
        selected_requests = [
            item
            for item in requests
            if baseline_start <= parse_timestamp(item["started_at"]) <= fault_end
        ]
        if not selected_requests:
            raise ValueError("selected verification run contains no client request records")

        target_start = datetime.now(UTC) + timedelta(seconds=2)
        shifted = [
            _shift_event(item, baseline_start, target_start)
            for item in selected
        ]
        shifted_requests = [
            _shift_request(item, baseline_start, target_start)
            for item in selected_requests
        ]
        shifted_baseline_end = target_start + (baseline_end - baseline_start)
        shifted_fault_start = target_start + (fault_start - baseline_start)
        shifted_fault_end = target_start + (fault_end - baseline_start)

        baseline_events = [
            item
            for item in shifted
            if parse_timestamp(item["timestamp"]) <= shifted_baseline_end
        ]
        fault_events = [
            item
            for item in shifted
            if shifted_fault_start < parse_timestamp(item["timestamp"]) <= shifted_fault_end
        ]
        baseline_requests = [
            item
            for item in shifted_requests
            if parse_timestamp(item["started_at"]) <= shifted_baseline_end
        ]
        fault_requests = [
            item
            for item in shifted_requests
            if shifted_fault_start < parse_timestamp(item["started_at"]) <= shifted_fault_end
        ]

        evaluations: list[dict[str, Any]] = []
        accepted = 0
        accepted_requests = 0
        with httpx.Client(timeout=20.0) as client:
            ready = client.get(f"{GATEWAY_URL}/api/v1/intelligence/ready")
            ready.raise_for_status()
            accepted += _post_events(client, baseline_events)
            accepted_requests += _post_requests(client, baseline_requests)

            chunk_start = shifted_fault_start
            while chunk_start < shifted_fault_end:
                chunk_end = min(chunk_start + timedelta(seconds=10), shifted_fault_end)
                chunk = [
                    item
                    for item in fault_events
                    if chunk_start < parse_timestamp(item["timestamp"]) <= chunk_end
                ]
                request_chunk = [
                    item
                    for item in fault_requests
                    if chunk_start < parse_timestamp(item["started_at"]) <= chunk_end
                ]
                if chunk:
                    accepted += _post_events(client, chunk)
                    if request_chunk:
                        accepted_requests += _post_requests(client, request_chunk)
                    response = client.post(
                        f"{GATEWAY_URL}/api/v1/intelligence/live/evaluate"
                    )
                    if response.status_code not in {200, 409}:
                        response.raise_for_status()
                    if response.status_code == 200:
                        evaluations.append(response.json())
                chunk_start = chunk_end

            listing = client.get(f"{GATEWAY_URL}/api/v1/intelligence/incidents")
            listing.raise_for_status()
            incidents = listing.json().get("incidents", [])
            live_inference = client.get(
                f"{GATEWAY_URL}/api/v1/intelligence/live/infer/60"
            )
            live_inference.raise_for_status()
            final_live_result = live_inference.json()["result"]

        if not incidents:
            raise ValueError("no incident was created from the observable fault replay")
        incident = incidents[0]
        classification = incident.get("classification")
        localization = incident.get("localization")
        severity = incident.get("severity")
        if not isinstance(classification, dict) or classification.get("label") in {None, "NORMAL"}:
            raise ValueError("incident never received an abnormal 60-second classification")
        if not isinstance(localization, dict) or not localization.get("probable_origin"):
            raise ValueError("incident never received probable-origin localization")
        if not isinstance(severity, dict) or not severity.get("level"):
            raise ValueError("incident never received live severity")
        final_live_classification = final_live_result.get("classification")
        if not isinstance(final_live_classification, dict):
            raise ValueError("final live 60-second classification is unavailable")
        if classification.get("label") != final_live_classification.get("label"):
            raise ValueError(
                "incident classification is stale relative to the latest 60-second window"
            )

        print("PASS: Phase 14E live incident lifecycle verified")
        print(f"Replay run: {manifest['run_id']}")
        print(f"Observable events accepted: {accepted}")
        print(f"Client request observations accepted: {accepted_requests}")
        print(f"Lifecycle evaluations: {len(evaluations)}")
        print(f"Incident ID: {incident['incident_id']}")
        print(f"State: {incident['state']}")
        print(f"Classification: {classification['label']}")
        print(
            "Final live 60s classification: "
            f"{final_live_classification['label']}"
        )
        print(f"Probable origin: {localization['probable_origin']}")
        print(f"Severity: {severity['level']} ({severity['score']})")
        print(f"Verifier target only (not sent to API): {TARGET_FAULT}")
        print("Ground-truth interval boundaries were used only to stage the verifier replay.")
        return 0
    except (OSError, ValueError, httpx.HTTPError, json.JSONDecodeError) as exc:
        print(f"FAIL: Phase 14E verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
