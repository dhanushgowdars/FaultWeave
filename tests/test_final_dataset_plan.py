from collections import Counter
from datetime import UTC, datetime

from datasets.final_plan import build_final_plan, validate_final_plan


def test_final_plan_freezes_counts_splits_and_durations() -> None:
    plan = build_final_plan(datetime(2026, 1, 1, tzinfo=UTC))
    assert validate_final_plan(plan) == []
    assert Counter(run.scenario_type for run in plan.runs) == {
        "normal": 60,
        "known_fault": 180,
        "sealed_unknown": 40,
    }
    assert Counter(run.split for run in plan.runs) == {
        "train": 144,
        "validation": 48,
        "test": 48,
        "evaluation_only": 40,
    }
    assert all(run.intervals[-1].end_seconds == 120 for run in plan.runs)


def test_sealed_unknowns_are_evaluation_only_and_ineligible() -> None:
    unknown = [
        run
        for run in build_final_plan(datetime(2026, 1, 1, tzinfo=UTC)).runs
        if run.scenario_type == "sealed_unknown"
    ]
    assert len(unknown) == 40
    assert all(run.split == "evaluation_only" for run in unknown)
    assert all(not run.training_eligible and not run.threshold_tuning_eligible for run in unknown)


def test_final_plan_is_deterministic_except_creation_time() -> None:
    first = build_final_plan(datetime(2026, 1, 1, tzinfo=UTC))
    second = build_final_plan(datetime(2026, 2, 1, tzinfo=UTC))
    assert first.model_dump(exclude={"created_at"}) == second.model_dump(exclude={"created_at"})
