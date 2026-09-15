from datetime import UTC, datetime

from datasets.final_executor import select_runs
from datasets.final_plan import build_final_plan


def test_default_partition_excludes_all_sealed_unknowns() -> None:
    plan = build_final_plan(datetime(2026, 1, 1, tzinfo=UTC))
    selected = select_runs(plan, "eligible", None, None)
    assert len(selected) == 240
    assert all(run.scenario_type != "sealed_unknown" for run in selected)


def test_evaluation_partition_contains_only_sealed_unknowns() -> None:
    plan = build_final_plan(datetime(2026, 1, 1, tzinfo=UTC))
    selected = select_runs(plan, "evaluation_only", None, None)
    assert len(selected) == 40
    assert all(run.scenario_type == "sealed_unknown" for run in selected)


def test_exact_run_cannot_cross_partition_boundary() -> None:
    plan = build_final_plan(datetime(2026, 1, 1, tzinfo=UTC))
    unknown = next(run for run in plan.runs if run.scenario_type == "sealed_unknown")
    try:
        select_runs(plan, "eligible", unknown.run_id, None)
    except ValueError as exc:
        assert "not available" in str(exc)
    else:
        raise AssertionError("sealed unknown crossed into eligible partition")
