from __future__ import annotations

import argparse
import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

from experiments.faults.adapters import (
    activate_infrastructure_fault,
    deactivate_infrastructure_fault,
    verify_stack_recovery,
)
from experiments.faults.lifecycle import FaultStateStore
from experiments.manifest import sha256_file, write_json
from experiments.runner import build_plan, current_git_commit, execute_plan, write_jsonl
from experiments.sealed_unknowns.lifecycle import SealedUnknownLease, SealedUnknownStateStore
from experiments.suite import calibrated_high_rps
from experiments.traffic_profiles import get_profile, resolve_rate_segments

from .artifacts import FinalRunManifest
from .contracts import FinalDatasetPlan, FinalRunSpec
from .final_plan import DEFAULT_OUTPUT, validate_final_plan
from .smoke_executor import (
    BASE_RPS,
    CONTROL_DIRECTORY,
    PROJECT_DIRECTORY,
    artifact,
    fault_rate,
    iso,
    observable_abnormality,
    run_interval,
    utc_now,
    validate_observable_artifacts,
    wait_for_current_run_events,
)

DATASET_ROOT = PROJECT_DIRECTORY / "data" / "datasets" / "final"


def load_plan(path: Path) -> FinalDatasetPlan:
    plan = FinalDatasetPlan.model_validate_json(path.read_text(encoding="utf-8"))
    errors = validate_final_plan(plan)
    if errors:
        raise ValueError("; ".join(errors))
    return plan


def select_runs(
    plan: FinalDatasetPlan, partition: str, run_id: str | None, limit: int | None
) -> list[FinalRunSpec]:
    runs = list(plan.runs)
    if partition == "eligible":
        runs = [run for run in runs if run.split != "evaluation_only"]
    elif partition == "evaluation_only":
        runs = [run for run in runs if run.split == "evaluation_only"]
    if run_id:
        runs = [run for run in runs if run.run_id == run_id]
        if not runs:
            raise ValueError(f"run {run_id!r} is not available in partition {partition!r}")
    return runs[:limit] if limit is not None else runs


def valid_existing_manifest(run: FinalRunSpec, plan_hash: str) -> bool:
    path = DATASET_ROOT / "manifests" / f"{run.run_id}.json"
    try:
        manifest = FinalRunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if manifest.plan_sha256 != plan_hash or not manifest.recovery_verified:
        return False
    if (manifest.split, manifest.training_eligible, manifest.threshold_tuning_eligible) != (
        run.split,
        run.training_eligible,
        run.threshold_tuning_eligible,
    ):
        return False
    return all(
        (PROJECT_DIRECTORY / item.path).exists()
        and sha256_file(PROJECT_DIRECTORY / item.path) == item.sha256
        for item in (manifest.requests, manifest.events, manifest.ground_truth)
    )


def execute_final_run(run: FinalRunSpec, plan_hash: str, gateway_url: str) -> FinalRunManifest:
    started_at = utc_now()
    attempt_id = f"{run.run_id}-a{uuid4().hex[:8]}"
    requests: list[dict] = []
    interval_truth: list[dict] = []
    recovery_verified = run.scenario_type == "normal"
    sequence = 1
    if run.scenario_type == "normal":
        profile = get_profile(run.profile)
        calibrated_limit = (
            calibrated_high_rps(PROJECT_DIRECTORY / "data" / "experiments")
            if run.profile in {"high_healthy", "short_burst"}
            else None
        )
        rps = (
            calibrated_limit
            if run.profile == "high_healthy"
            else profile.target_rps
        )
        maximum_rps = calibrated_limit if run.profile == "short_burst" else None
        planned = build_plan(
            profile,
            run.seed,
            run.intervals[0].duration_seconds,
            rps,
            maximum_rps,
        )
        interval_started = utc_now()
        results = asyncio.run(
            execute_plan(planned, gateway_url, run.run_id, run.seed, 150, attempt_id)
        )
        interval_ended = utc_now()
        for result in results:
            result["interval"] = "normal"
        requests.extend(results)
        interval_truth.append(
            {
                "name": "normal",
                "started_at": iso(interval_started),
                "ended_at": iso(interval_ended),
                "rate_schedule": [
                    {
                        "offset_seconds": segment.offset_seconds,
                        "duration_seconds": segment.duration_seconds,
                        "target_rps": min(rps * segment.multiplier, maximum_rps)
                        if maximum_rps is not None
                        else rps * segment.multiplier,
                    }
                    for segment in resolve_rate_segments(
                        profile, run.intervals[0].duration_seconds
                    )
                ],
            }
        )
    else:
        baseline, fault_interval, recovery = run.intervals
        results, interval_started, interval_ended = asyncio.run(
            run_interval(run, baseline, sequence, BASE_RPS, gateway_url, attempt_id)
        )
        requests.extend(results)
        sequence += len(results)
        interval_truth.append(
            {
                "name": "baseline",
                "started_at": iso(interval_started),
                "ended_at": iso(interval_ended),
            }
        )
        fault_started = utc_now()
        if run.scenario_type == "known_fault":
            store = FaultStateStore(CONTROL_DIRECTORY)
            activation = store.acquire(
                run_id=run.run_id,
                fault_id=run.fault_id,
                target=run.target,
                intensity=run.intensity,
                duration_seconds=fault_interval.duration_seconds,
            )
            try:
                activate_infrastructure_fault(activation)
                rps, scenario = fault_rate(run)
                results, _, fault_ended = asyncio.run(
                    run_interval(
                        run, fault_interval, sequence, rps, gateway_url, attempt_id, scenario
                    )
                )
            finally:
                deactivate_infrastructure_fault(activation)
                store.release(activation.activation_id)
                verify_stack_recovery()
        else:
            store = SealedUnknownStateStore(CONTROL_DIRECTORY)
            activation = store.acquire(
                run_id=run.run_id,
                fault_id=run.fault_id,
                target=run.target,
                intensity=run.intensity,
                duration_seconds=fault_interval.duration_seconds,
            )
            with SealedUnknownLease(store, activation, verify_stack_recovery):
                results, _, fault_ended = asyncio.run(
                    run_interval(run, fault_interval, sequence, BASE_RPS, gateway_url, attempt_id)
                )
        requests.extend(results)
        sequence += len(results)
        interval_truth.append(
            {"name": "fault", "started_at": iso(fault_started), "ended_at": iso(fault_ended)}
        )
        results, interval_started, interval_ended = asyncio.run(
            run_interval(run, recovery, sequence, BASE_RPS, gateway_url, attempt_id)
        )
        requests.extend(results)
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
    expected_ids = {item["request_id"] for item in requests}
    events = wait_for_current_run_events(run.run_id, expected_ids, started_at)
    errors = validate_observable_artifacts(run, requests, events)
    if run.scenario_type != "normal" and not observable_abnormality(requests):
        errors.append("fault interval produced no observable abnormality")
    if not recovery_verified:
        errors.append("recovery interval did not return to successful normal flow")
    if errors:
        raise RuntimeError("; ".join(errors[:10]))
    run_root = DATASET_ROOT / "raw" / run.run_id
    requests_path, events_path = run_root / "requests.jsonl", run_root / "events.jsonl"
    truth_path = DATASET_ROOT / "ground_truth" / f"{run.run_id}.json"
    observable_requests = [
        {key: value for key, value in item.items() if key != "interval"} for item in requests
    ]
    write_jsonl(requests_path, observable_requests)
    write_jsonl(events_path, events)
    truth = {
        "schema_version": "1.0",
        "dataset_id": "faultweave-final-dataset-v1",
        "run_id": run.run_id,
        "attempt_id": attempt_id,
        "scenario_type": run.scenario_type,
        "split": run.split,
        "fault_id": run.fault_id.value if run.fault_id else None,
        "target": run.target,
        "intensity": run.intensity.value if run.intensity else None,
        "intervals": interval_truth,
        "training_eligible": run.training_eligible,
        "threshold_tuning_eligible": run.threshold_tuning_eligible,
    }
    write_json(truth_path, truth)
    manifest = FinalRunManifest(
        run_id=run.run_id,
        attempt_id=attempt_id,
        scenario_type=run.scenario_type,
        split=run.split,
        training_eligible=run.training_eligible,
        threshold_tuning_eligible=run.threshold_tuning_eligible,
        started_at=started_at,
        ended_at=utc_now(),
        git_commit=current_git_commit(),
        plan_sha256=plan_hash,
        requests=artifact(requests_path, len(observable_requests)),
        events=artifact(events_path, len(events)),
        ground_truth=artifact(truth_path, 1),
        recovery_verified=recovery_verified,
    )
    write_json(DATASET_ROOT / "manifests" / f"{run.run_id}.json", manifest)
    return manifest


def execute_final_plan(
    plan_path: Path, gateway_url: str, partition: str, run_id: str | None, limit: int | None
) -> tuple[int, int]:
    plan = load_plan(plan_path)
    plan_hash = sha256_file(plan_path)
    completed = resumed = 0
    for run in select_runs(plan, partition, run_id, limit):
        if valid_existing_manifest(run, plan_hash):
            resumed += 1
            print(f"RESUME: {run.run_id}")
        else:
            print(f"RUN: {run.run_id}")
            execute_final_run(run, plan_hash, gateway_url)
            completed += 1
    return completed, resumed


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute the Phase 6 final dataset")
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gateway-url", default="http://localhost:18110")
    parser.add_argument(
        "--partition", choices=("eligible", "evaluation_only", "all"), default="eligible"
    )
    parser.add_argument("--run-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        plan = load_plan(args.plan)
        selected = select_runs(plan, args.partition, args.run_id, args.limit)
        if args.dry_run:
            for run in selected:
                print(f"PLAN: {run.run_id} split={run.split}")
            print(f"PASS: Planned {len(selected)} final runs without sending traffic")
            return 0
        completed, resumed = execute_final_plan(
            args.plan, args.gateway_url, args.partition, args.run_id, args.limit
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"PASS: Final execution completed={completed} resumed={resumed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
