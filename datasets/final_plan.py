from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from experiments.faults.catalog import FaultFamily, FaultIntensity, get_known_fault
from experiments.manifest import write_json
from experiments.sealed_unknowns.catalog import SEALED_UNKNOWN_SCENARIOS, SealedUnknownFamily
from experiments.traffic_profiles import PROFILES

from .contracts import FinalDatasetPlan, FinalRunSpec, IntervalSpec

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_DIRECTORY / "data" / "datasets" / "final" / "plan.json"
PLAN_SEED = 60000
NORMAL_DURATION_SECONDS = 120.0
BASELINE_DURATION_SECONDS = 30.0
FAULT_DURATION_SECONDS = 60.0
RECOVERY_DURATION_SECONDS = 30.0
PROFILE_SPLITS = ((8, 2, 2), (7, 3, 2), (7, 3, 2), (7, 2, 3), (7, 2, 3))


def normal_intervals() -> tuple[IntervalSpec, ...]:
    return (IntervalSpec(name="normal", offset_seconds=0, duration_seconds=120),)


def fault_intervals() -> tuple[IntervalSpec, ...]:
    return (
        IntervalSpec(name="baseline", offset_seconds=0, duration_seconds=30),
        IntervalSpec(name="fault", offset_seconds=30, duration_seconds=60),
        IntervalSpec(name="recovery", offset_seconds=90, duration_seconds=30),
    )


def assigned_split(replicate: int, counts: tuple[int, int, int]) -> str:
    train, validation, _ = counts
    if replicate <= train:
        return "train"
    if replicate <= train + validation:
        return "validation"
    return "test"


def build_final_plan(created_at: datetime | None = None) -> FinalDatasetPlan:
    runs: list[FinalRunSpec] = []
    for profile_index, profile in enumerate(PROFILES):
        for replicate in range(1, 13):
            runs.append(
                FinalRunSpec(
                    run_id=f"final-normal-{profile.replace('_', '-')}-{replicate:02d}",
                    scenario_type="normal",
                    split=assigned_split(replicate, PROFILE_SPLITS[profile_index]),
                    replicate=replicate,
                    seed=PLAN_SEED + profile_index * 100 + replicate,
                    profile=profile,
                    intervals=normal_intervals(),
                    training_eligible=True,
                    threshold_tuning_eligible=True,
                )
            )
    intensities = tuple(FaultIntensity)
    for family_index, family in enumerate(FaultFamily, start=1):
        definition = get_known_fault(family)
        slug = family.value.lower().replace("_", "-")
        for replicate in range(1, 21):
            intensity = (
                FaultIntensity.HIGH
                if family is FaultFamily.CONNECTION_POOL_EXHAUSTION
                else intensities[(replicate - 1) % 3]
            )
            runs.append(
                FinalRunSpec(
                    run_id=f"final-known-{slug}-{replicate:02d}",
                    scenario_type="known_fault",
                    split=assigned_split(replicate, (12, 4, 4)),
                    replicate=replicate,
                    seed=PLAN_SEED + 1000 + family_index * 100 + replicate,
                    profile="medium",
                    fault_id=family,
                    target=definition.allowed_targets[
                        (replicate - 1) % len(definition.allowed_targets)
                    ],
                    intensity=intensity,
                    intervals=fault_intervals(),
                    training_eligible=True,
                    threshold_tuning_eligible=True,
                )
            )
    for family_index, family in enumerate(SealedUnknownFamily, start=1):
        definition = SEALED_UNKNOWN_SCENARIOS[family]
        slug = family.value.lower().replace("_", "-")
        for replicate in range(1, 21):
            runs.append(
                FinalRunSpec(
                    run_id=f"final-unknown-{slug}-{replicate:02d}",
                    scenario_type="sealed_unknown",
                    split="evaluation_only",
                    replicate=replicate,
                    seed=PLAN_SEED + 3000 + family_index * 100 + replicate,
                    profile="medium",
                    fault_id=family,
                    target=definition.allowed_targets[
                        (replicate - 1) % len(definition.allowed_targets)
                    ],
                    intensity=intensities[(replicate - 1) % 3],
                    intervals=fault_intervals(),
                    training_eligible=False,
                    threshold_tuning_eligible=False,
                )
            )
    return FinalDatasetPlan(
        created_at=created_at or datetime.now(UTC), plan_seed=PLAN_SEED, runs=tuple(runs)
    )


def validate_final_plan(plan: FinalDatasetPlan) -> list[str]:
    errors: list[str] = []
    scenarios = Counter(run.scenario_type for run in plan.runs)
    splits = Counter(run.split for run in plan.runs)
    known = Counter(run.fault_id for run in plan.runs if run.scenario_type == "known_fault")
    unknown = Counter(run.fault_id for run in plan.runs if run.scenario_type == "sealed_unknown")
    if scenarios != {"normal": 60, "known_fault": 180, "sealed_unknown": 40}:
        errors.append("scenario counts differ from frozen matrix")
    if splits != {"train": 144, "validation": 48, "test": 48, "evaluation_only": 40}:
        errors.append("whole-run split counts differ from 60/20/20 contract")
    if set(known) != set(FaultFamily) or any(value != 20 for value in known.values()):
        errors.append("known-fault coverage must be 20 per class")
    if set(unknown) != set(SealedUnknownFamily) or any(value != 20 for value in unknown.values()):
        errors.append("sealed-unknown coverage must be 20 per family")
    if any(
        run.training_eligible or run.threshold_tuning_eligible
        for run in plan.runs
        if run.scenario_type == "sealed_unknown"
    ):
        errors.append("sealed unknown eligibility violation")
    return errors


def write_final_plan(output: Path = DEFAULT_OUTPUT) -> FinalDatasetPlan:
    plan = build_final_plan()
    errors = validate_final_plan(plan)
    if errors:
        raise RuntimeError("; ".join(errors))
    if output.exists():
        try:
            existing = FinalDatasetPlan.model_validate_json(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if existing and existing.model_dump(exclude={"created_at"}) == plan.model_dump(
            exclude={"created_at"}
        ):
            return existing
    write_json(output, plan)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Create deterministic Phase 6 final dataset plan")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        plan = write_final_plan(args.output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print("PASS: Phase 6 final-dataset plan created")
    print(f"Dataset ID: {plan.dataset_id}")
    print("Runs: 280 (normal=60, known=180, sealed-unknown=40)")
    print("Eligible split: train=144, validation=48, test=48")
    print("Sealed unknown: evaluation_only=40")
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
