from datetime import UTC, datetime

from datasets.smoke_plan import build_smoke_plan, validate_smoke_plan, write_smoke_plan
from experiments.faults.catalog import FaultFamily, FaultIntensity
from experiments.manifest import sha256_file


def test_smoke_plan_has_exact_frozen_matrix() -> None:
    plan = build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC))
    assert len(plan.runs) == 55
    assert len([run for run in plan.runs if run.scenario_type == "normal"]) == 10
    for family in FaultFamily:
        assert len([run for run in plan.runs if run.fault_id == family]) == 5
    assert validate_smoke_plan(plan) == []


def test_smoke_plan_is_reproducible_except_creation_time() -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    first = build_smoke_plan(created_at)
    second = build_smoke_plan(created_at)
    assert first == second


def test_fault_runs_have_contiguous_three_part_intervals() -> None:
    plan = build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC))
    for run in plan.runs:
        if run.scenario_type != "known_fault":
            continue
        assert [interval.name for interval in run.intervals] == [
            "baseline", "fault", "recovery"
        ]
        assert run.intervals[0].offset_seconds == 0
        assert run.intervals[0].end_seconds == run.intervals[1].offset_seconds
        assert run.intervals[1].end_seconds == run.intervals[2].offset_seconds


def test_sealed_unknown_identifiers_are_absent() -> None:
    serialized = build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC)).model_dump_json()
    assert "INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE" not in serialized
    assert "LATENCY_JITTER_PARTIAL_DEGRADATION" not in serialized


def test_pool_exhaustion_uses_full_pool_pressure() -> None:
    plan = build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC))
    pool_runs = [
        run for run in plan.runs if run.fault_id is FaultFamily.CONNECTION_POOL_EXHAUSTION
    ]
    assert len(pool_runs) == 5
    assert {run.intensity for run in pool_runs} == {FaultIntensity.HIGH}


def test_writing_identical_plan_preserves_hash(tmp_path) -> None:
    path = tmp_path / "plan.json"
    first = write_smoke_plan(path)
    first_hash = sha256_file(path)
    second = write_smoke_plan(path)
    assert second.created_at == first.created_at
    assert sha256_file(path) == first_hash
