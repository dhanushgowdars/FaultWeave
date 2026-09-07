from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from experiments.faults.adapters import (
    activate_infrastructure_fault,
    deactivate_infrastructure_fault,
    verify_stack_recovery,
)
from experiments.faults.catalog import FaultFamily
from experiments.faults.lifecycle import FaultStateStore
from experiments.manifest import sha256_file, write_json
from experiments.runner import (
    PlannedRequest,
    build_plan,
    collect_run_events,
    current_git_commit,
    execute_plan,
    validate_correlated_run,
    write_jsonl,
)
from experiments.suite import calibrated_high_rps
from experiments.traffic_profiles import get_profile

from .artifacts import DatasetArtifact, SmokeRunManifest
from .contracts import DatasetPlan, IntervalSpec, RunSpec
from .smoke_plan import DEFAULT_OUTPUT, validate_smoke_plan

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_DIRECTORY / "data" / "datasets" / "smoke"
CONTROL_DIRECTORY = PROJECT_DIRECTORY / "data" / "fault-control"
BASE_RPS = 2.0
AUTH_FAILURE_RPS = 20.0
POOL_RPS = 3.0
FORBIDDEN_EVENT_KEYS = {"fault_active", "fault_id", "fault_label", "fault_origin"}
SECRET_MARKERS = ("faultweave-demo", "invalid-demo-password", "service-token", "bearer ey")


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def load_plan(path: Path) -> DatasetPlan:
    plan = DatasetPlan.model_validate_json(path.read_text(encoding="utf-8"))
    errors = validate_smoke_plan(plan)
    if errors:
        raise ValueError("; ".join(errors))
    return plan


def plan_requests(
    run: RunSpec,
    interval: IntervalSpec,
    sequence_start: int,
    target_rps: float,
    scenario: str = "valid",
) -> list[PlannedRequest]:
    count = max(1, round(interval.duration_seconds * target_rps))
    return [
        PlannedRequest(
            sequence=sequence_start + index,
            offset_seconds=index / target_rps,
            scenario=scenario,
            amount_minor=1000 + ((run.seed + index) % 50_000),
        )
        for index in range(count)
    ]


async def run_interval(
    run: RunSpec,
    interval: IntervalSpec,
    sequence_start: int,
    target_rps: float,
    gateway_url: str,
    request_namespace: str,
    scenario: str = "valid",
) -> tuple[list[dict[str, Any]], datetime, datetime]:
    started_at = utc_now()
    plan = plan_requests(run, interval, sequence_start, target_rps, scenario)
    results = await execute_plan(
        plan,
        gateway_url,
        run.run_id,
        run.seed,
        150,
        request_namespace,
    )
    ended_at = utc_now()
    for result in results:
        result["interval"] = interval.name
    return results, started_at, ended_at


def fault_rate(run: RunSpec) -> tuple[float, str]:
    if run.fault_id is FaultFamily.AUTHENTICATION_FAILURE_BURST:
        return AUTH_FAILURE_RPS, "invalid_login"
    if run.fault_id is FaultFamily.HIGH_LOAD:
        multiplier = {"mild": 1.5, "medium": 2.0, "high": 2.5}[run.intensity.value]
        return calibrated_high_rps(PROJECT_DIRECTORY / "data" / "experiments") * multiplier, "valid"
    if run.fault_id is FaultFamily.CONNECTION_POOL_EXHAUSTION:
        return POOL_RPS, "valid"
    return BASE_RPS, "valid"


def artifact(path: Path, count: int) -> DatasetArtifact:
    return DatasetArtifact(
        path=path.relative_to(PROJECT_DIRECTORY).as_posix(),
        sha256=sha256_file(path),
        record_count=count,
    )


def contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(FORBIDDEN_EVENT_KEYS.intersection(value)) or any(
            contains_forbidden_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(contains_forbidden_key(item) for item in value)
    return False


def validate_observable_artifacts(
    run: RunSpec, requests: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    if not requests:
        errors.append("request artifact is empty")
    if not events:
        errors.append("event artifact is empty")
    if any(event.get("run_id") != run.run_id for event in events):
        errors.append("event artifact contains mixed run IDs")
    if any(contains_forbidden_key(event) for event in events):
        errors.append("protected fault metadata leaked into observable events")
    serialized = json.dumps({"requests": requests, "events": events}).lower()
    if any(marker in serialized for marker in SECRET_MARKERS):
        errors.append("secret marker leaked into observable artifacts")
    non_fault = [item for item in requests if item["interval"] != "fault"]
    if run.scenario_type == "normal":
        errors.extend(validate_correlated_run(requests, events))
    elif non_fault:
        errors.extend(validate_correlated_run(non_fault, events))
    return list(dict.fromkeys(errors))


def observable_abnormality(requests: list[dict[str, Any]]) -> bool:
    fault_requests = [item for item in requests if item["interval"] == "fault"]
    if not fault_requests:
        return False
    return any(
        item["transport_error"] is not None
        or item["status_code"] is None
        or item["status_code"] >= 400
        or item["latency_ms"] >= 200
        for item in fault_requests
    )


def wait_for_current_run_events(
    run_id: str, expected_request_ids: set[str], attempt_started_at: datetime
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(10):
        collected = collect_run_events(run_id)
        events = [
            event
            for event in collected
            if datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
            >= attempt_started_at
        ]
        observed = {event.get("request_id") for event in events}
        if expected_request_ids.issubset(observed):
            return events
        time.sleep(0.5)
    return events


def valid_existing_manifest(run: RunSpec, plan_hash: str) -> bool:
    path = DATASET_ROOT / "manifests" / f"{run.run_id}.json"
    try:
        manifest = SmokeRunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if manifest.plan_sha256 != plan_hash or not manifest.recovery_verified:
        return False
    for item in (manifest.requests, manifest.events, manifest.ground_truth):
        artifact_path = PROJECT_DIRECTORY / item.path
        if not artifact_path.exists() or sha256_file(artifact_path) != item.sha256:
            return False
    return True


def execute_run(run: RunSpec, plan_hash: str, gateway_url: str) -> SmokeRunManifest:
    started_at = utc_now()
    attempt_id = f"{run.run_id}-a{uuid4().hex[:8]}"
    all_requests: list[dict[str, Any]] = []
    interval_truth: list[dict[str, Any]] = []
    recovery_verified = run.scenario_type == "normal"
    sequence = 1

    if run.scenario_type == "normal":
        profile = get_profile(run.profile)
        rps = (
            calibrated_high_rps(PROJECT_DIRECTORY / "data" / "experiments")
            if run.profile == "high_healthy"
            else profile.target_rps
        )
        planned = build_plan(profile, run.seed, run.intervals[0].duration_seconds, rps)
        interval_started = utc_now()
        results = asyncio.run(
            execute_plan(
                planned,
                gateway_url,
                run.run_id,
                run.seed,
                150,
                attempt_id,
            )
        )
        interval_ended = utc_now()
        for result in results:
            result["interval"] = "normal"
        all_requests.extend(results)
        interval_truth.append(
            {"name": "normal", "started_at": iso(interval_started), "ended_at": iso(interval_ended)}
        )
    else:
        baseline, fault_interval, recovery = run.intervals
        results, interval_started, interval_ended = asyncio.run(
            run_interval(run, baseline, sequence, BASE_RPS, gateway_url, attempt_id)
        )
        all_requests.extend(results)
        sequence += len(results)
        interval_truth.append(
            {
                "name": "baseline",
                "started_at": iso(interval_started),
                "ended_at": iso(interval_ended),
            }
        )

        store = FaultStateStore(CONTROL_DIRECTORY)
        activation = store.acquire(
            run_id=run.run_id,
            fault_id=run.fault_id,
            target=run.target,
            intensity=run.intensity,
            duration_seconds=fault_interval.duration_seconds,
        )
        fault_started = utc_now()
        try:
            activate_infrastructure_fault(activation)
            rps, scenario = fault_rate(run)
            results, _, fault_ended = asyncio.run(
                run_interval(
                    run,
                    fault_interval,
                    sequence,
                    rps,
                    gateway_url,
                    attempt_id,
                    scenario,
                )
            )
            all_requests.extend(results)
            sequence += len(results)
        finally:
            deactivate_infrastructure_fault(activation)
            store.release(activation.activation_id)
            verify_stack_recovery()
        interval_truth.append(
            {"name": "fault", "started_at": iso(fault_started), "ended_at": iso(fault_ended)}
        )

        results, interval_started, interval_ended = asyncio.run(
            run_interval(run, recovery, sequence, BASE_RPS, gateway_url, attempt_id)
        )
        all_requests.extend(results)
        interval_truth.append(
            {
                "name": "recovery",
                "started_at": iso(interval_started),
                "ended_at": iso(interval_ended),
            }
        )
        recovery_verified = all(
            item["status_code"] == 200 and item["expected_outcome"] for item in results
        )

    expected_ids = {item["request_id"] for item in all_requests}
    events = wait_for_current_run_events(run.run_id, expected_ids, started_at)
    errors = validate_observable_artifacts(run, all_requests, events)
    if run.scenario_type == "known_fault" and not observable_abnormality(all_requests):
        errors.append("fault interval produced no observable abnormality")
    if not recovery_verified:
        errors.append("recovery interval did not return to successful normal flow")
    if errors:
        raise RuntimeError("; ".join(errors[:10]))

    run_root = DATASET_ROOT / "raw" / run.run_id
    requests_path = run_root / "requests.jsonl"
    events_path = run_root / "events.jsonl"
    ground_truth_path = DATASET_ROOT / "ground_truth" / f"{run.run_id}.json"
    observable_requests = [
        {key: value for key, value in item.items() if key != "interval"}
        for item in all_requests
    ]
    write_jsonl(requests_path, observable_requests)
    write_jsonl(events_path, events)
    ground_truth = {
        "schema_version": "1.0",
        "dataset_id": "smoke-dataset-v1",
        "run_id": run.run_id,
        "attempt_id": attempt_id,
        "scenario_type": run.scenario_type,
        "fault_id": run.fault_id.value if run.fault_id else None,
        "target": run.target,
        "intensity": run.intensity.value if run.intensity else None,
        "intervals": interval_truth,
        "training_eligible": True,
    }
    write_json(ground_truth_path, ground_truth)
    manifest = SmokeRunManifest(
        run_id=run.run_id,
        attempt_id=attempt_id,
        scenario_type=run.scenario_type,
        started_at=started_at,
        ended_at=utc_now(),
        git_commit=current_git_commit(),
        plan_sha256=plan_hash,
        requests=artifact(requests_path, len(observable_requests)),
        events=artifact(events_path, len(events)),
        ground_truth=artifact(ground_truth_path, 1),
        recovery_verified=recovery_verified,
    )
    write_json(DATASET_ROOT / "manifests" / f"{run.run_id}.json", manifest)
    return manifest


def select_runs(plan: DatasetPlan, run_id: str | None, limit: int | None) -> list[RunSpec]:
    runs = list(plan.runs)
    if run_id:
        runs = [run for run in runs if run.run_id == run_id]
        if not runs:
            raise ValueError(f"run {run_id!r} is not in the frozen smoke plan")
    return runs[:limit] if limit is not None else runs


def execute_smoke_plan(
    plan_path: Path, gateway_url: str, run_id: str | None = None, limit: int | None = None
) -> tuple[int, int]:
    plan = load_plan(plan_path)
    plan_hash = sha256_file(plan_path)
    completed = resumed = 0
    for run in select_runs(plan, run_id, limit):
        if valid_existing_manifest(run, plan_hash):
            resumed += 1
            print(f"RESUME: {run.run_id}")
            continue
        print(f"RUN: {run.run_id}")
        execute_run(run, plan_hash, gateway_url)
        completed += 1
    return completed, resumed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute the Phase 5 smoke dataset")
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gateway-url", default="http://localhost:18110")
    parser.add_argument("--run-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    return args


def main() -> int:
    args = parse_args()
    try:
        plan = load_plan(args.plan)
        selected = select_runs(plan, args.run_id, args.limit)
        if args.dry_run:
            for run in selected:
                print(f"PLAN: {run.run_id}")
            print(f"PASS: Planned {len(selected)} smoke runs without sending traffic")
            return 0
        completed, resumed = execute_smoke_plan(
            args.plan, args.gateway_url, args.run_id, args.limit
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"PASS: Smoke execution completed={completed} resumed={resumed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
