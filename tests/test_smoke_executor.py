from datetime import UTC, datetime

import pytest

from datasets.smoke_executor import (
    observable_abnormality,
    plan_requests,
    select_runs,
    validate_observable_artifacts,
    wait_for_current_run_events,
)
from datasets.smoke_plan import build_smoke_plan


def test_run_selection_supports_limit_and_exact_run() -> None:
    plan = build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC))
    assert len(select_runs(plan, None, 2)) == 2
    selected = select_runs(plan, plan.runs[12].run_id, None)
    assert selected == [plan.runs[12]]
    with pytest.raises(ValueError, match="not in the frozen smoke plan"):
        select_runs(plan, "not-a-run", None)


def test_interval_request_plan_has_unique_global_sequences() -> None:
    run = build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC)).runs[10]
    baseline = plan_requests(run, run.intervals[0], 1, 2)
    fault = plan_requests(run, run.intervals[1], len(baseline) + 1, 2)
    sequences = [item.sequence for item in baseline + fault]
    assert sequences == list(range(1, len(sequences) + 1))


def test_observable_abnormality_requires_fault_interval_signal() -> None:
    assert observable_abnormality(
        [
            {"interval": "baseline", "status_code": 503, "transport_error": None, "latency_ms": 5},
            {"interval": "fault", "status_code": 200, "transport_error": None, "latency_ms": 500},
        ]
    )
    assert not observable_abnormality(
        [{"interval": "fault", "status_code": 200, "transport_error": None, "latency_ms": 20}]
    )


def test_event_validation_rejects_label_and_secret_leakage() -> None:
    run = build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC)).runs[10]
    errors = validate_observable_artifacts(
        run,
        [{"interval": "fault", "request_id": "request-1"}],
        [{"run_id": run.run_id, "fault_id": "DATABASE_HIGH_LATENCY", "value": "service-token"}],
    )
    assert any("fault metadata" in error for error in errors)
    assert any("secret" in error for error in errors)


def test_retry_collection_excludes_events_from_earlier_attempt(monkeypatch) -> None:
    attempt = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "datasets.smoke_executor.collect_run_events",
        lambda _: [
            {"request_id": "request-1", "timestamp": "2026-01-01T11:59:59Z"},
            {"request_id": "request-1", "timestamp": "2026-01-01T12:00:01Z"},
        ],
    )
    events = wait_for_current_run_events("run", {"request-1"}, attempt)
    assert len(events) == 1
    assert events[0]["timestamp"] == "2026-01-01T12:00:01Z"


def test_attempt_namespace_fits_manifest_contract_for_every_smoke_run() -> None:
    plan = build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC))
    attempt_ids = [f"{run.run_id}-a12345678" for run in plan.runs]

    assert max(map(len, attempt_ids)) <= 75
    assert len(attempt_ids) == len(set(attempt_ids))
