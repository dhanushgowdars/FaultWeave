from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from experiments.faults.catalog import (
    CORE_KNOWN_FAULTS,
    EXTENDED_KNOWN_FAULTS,
    FaultFamily,
    FaultIntensity,
)
from experiments.manifest import write_json
from experiments.seeds import OFFICIAL_NORMAL_SEEDS

from .contracts import DatasetPlan, IntervalSpec, RunSpec

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_DIRECTORY / "data" / "datasets" / "smoke" / "plan.json"
PLAN_SEED = 50000
NORMAL_DURATION_SECONDS = 12.0
BASELINE_DURATION_SECONDS = 5.0
FAULT_DURATION_SECONDS = 8.0
RECOVERY_DURATION_SECONDS = 5.0


def normal_intervals() -> tuple[IntervalSpec, ...]:
    return (
        IntervalSpec(name="normal", offset_seconds=0, duration_seconds=NORMAL_DURATION_SECONDS),
    )


def fault_intervals() -> tuple[IntervalSpec, ...]:
    return (
        IntervalSpec(name="baseline", offset_seconds=0, duration_seconds=BASELINE_DURATION_SECONDS),
        IntervalSpec(
            name="fault",
            offset_seconds=BASELINE_DURATION_SECONDS,
            duration_seconds=FAULT_DURATION_SECONDS,
        ),
        IntervalSpec(
            name="recovery",
            offset_seconds=BASELINE_DURATION_SECONDS + FAULT_DURATION_SECONDS,
            duration_seconds=RECOVERY_DURATION_SECONDS,
        ),
    )


def build_smoke_plan(created_at: datetime | None = None) -> DatasetPlan:
    runs: list[RunSpec] = []
    normal_index = 0
    for profile in OFFICIAL_NORMAL_SEEDS:
        for seed in OFFICIAL_NORMAL_SEEDS[profile][:2]:
            normal_index += 1
            runs.append(
                RunSpec(
                    run_id=f"smoke-normal-{profile.replace('_', '-')}-{normal_index:02d}",
                    scenario_type="normal",
                    replicate=1 if normal_index % 2 else 2,
                    seed=seed,
                    profile=profile,
                    intervals=normal_intervals(),
                )
            )

    definitions = {**CORE_KNOWN_FAULTS, **EXTENDED_KNOWN_FAULTS}
    intensities = tuple(FaultIntensity)
    for family_index, family in enumerate(FaultFamily, start=1):
        definition = definitions[family]
        slug = family.value.lower().replace("_", "-")
        for replicate in range(1, 6):
            target = definition.allowed_targets[(replicate - 1) % len(definition.allowed_targets)]
            intensity = (
                FaultIntensity.HIGH
                if family is FaultFamily.CONNECTION_POOL_EXHAUSTION
                else intensities[(replicate - 1) % len(intensities)]
            )
            runs.append(
                RunSpec(
                    run_id=f"smoke-known-{slug}-{replicate:02d}",
                    scenario_type="known_fault",
                    replicate=replicate,
                    seed=PLAN_SEED + family_index * 100 + replicate,
                    profile="medium",
                    fault_id=family,
                    target=target,
                    intensity=intensity,
                    intervals=fault_intervals(),
                )
            )

    return DatasetPlan(
        dataset_id="smoke-dataset-v1",
        created_at=created_at or datetime.now(UTC),
        plan_seed=PLAN_SEED,
        runs=tuple(runs),
    )


def validate_smoke_plan(plan: DatasetPlan) -> list[str]:
    errors: list[str] = []
    normal = [run for run in plan.runs if run.scenario_type == "normal"]
    known = [run for run in plan.runs if run.scenario_type == "known_fault"]
    counts = Counter(run.fault_id for run in known)
    expected = set(FaultFamily)
    if len(normal) != 10:
        errors.append(f"normal run count is {len(normal)}; expected 10")
    if len(known) != 45:
        errors.append(f"known-fault run count is {len(known)}; expected 45")
    if set(counts) != expected:
        errors.append("known-fault class coverage differs from frozen registry")
    for family in expected:
        if counts[family] != 5:
            errors.append(f"{family.value} has {counts[family]} runs; expected 5")
    if any(not run.training_eligible for run in plan.runs):
        errors.append("smoke training matrix contains an ineligible run")
    serialized = plan.model_dump_json()
    if "INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE" in serialized:
        errors.append("sealed intermittent-connection scenario leaked into smoke plan")
    if "LATENCY_JITTER_PARTIAL_DEGRADATION" in serialized:
        errors.append("sealed latency-jitter scenario leaked into smoke plan")
    return errors


def write_smoke_plan(output: Path = DEFAULT_OUTPUT) -> DatasetPlan:
    plan = build_smoke_plan()
    errors = validate_smoke_plan(plan)
    if errors:
        raise RuntimeError("; ".join(errors))
    if output.exists():
        try:
            existing = DatasetPlan.model_validate_json(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if existing is not None:
            existing_contract = existing.model_dump(exclude={"created_at"})
            current_contract = plan.model_dump(exclude={"created_at"})
            if existing_contract == current_contract:
                return existing
    write_json(output, plan)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the deterministic Phase 5 smoke plan")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        plan = write_smoke_plan(args.output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print("PASS: Phase 5 smoke-dataset plan created")
    print(f"Dataset ID: {plan.dataset_id}")
    print("Normal runs: 10")
    print("Known-fault runs: 45 (5 per class)")
    print("Sealed unknown runs: 0")
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
