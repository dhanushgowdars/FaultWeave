from __future__ import annotations

from datetime import UTC, datetime, timedelta

from datasets.artifacts import FinalRunManifest
from datasets.final_features import (
    FEATURE_NAMES,
    _bucket_records,
    _windows_from_truth,
    feature_record,
    validate_feature_report,
)


def manifest() -> FinalRunManifest:
    artifact = {"path": "data/placeholder.jsonl", "sha256": "0" * 64, "record_count": 1}
    return FinalRunManifest(
        run_id="final-known-example-01",
        attempt_id="final-known-example-01-a1234567",
        scenario_type="known_fault",
        split="train",
        training_eligible=True,
        threshold_tuning_eligible=True,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, tzinfo=UTC),
        git_commit=None,
        plan_sha256="0" * 64,
        requests=artifact,
        events=artifact,
        ground_truth={**artifact, "path": "data/placeholder.json"},
        recovery_verified=True,
    )


def truth() -> dict[str, object]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "fault_id": "DATABASE_HIGH_LATENCY",
        "intervals": [
            {
                "name": "baseline",
                "started_at": start.isoformat(),
                "ended_at": (start + timedelta(seconds=30)).isoformat(),
            },
            {
                "name": "fault",
                "started_at": (start + timedelta(seconds=30)).isoformat(),
                "ended_at": (start + timedelta(seconds=90)).isoformat(),
            },
            {
                "name": "recovery",
                "started_at": (start + timedelta(seconds=90)).isoformat(),
                "ended_at": (start + timedelta(seconds=120)).isoformat(),
            },
        ],
    }


def request(at: datetime) -> dict[str, object]:
    return {
        "started_at": at.isoformat(),
        "latency_ms": 100.0,
        "status_code": 200,
        "expected_outcome": True,
        "transport_error": None,
        "scenario": "valid",
    }


def event(at: datetime) -> dict[str, object]:
    return {
        "timestamp": at.isoformat(),
        "service": "gateway",
        "event_type": "transaction_flow_started",
        "level": "INFO",
        "success": True,
        "latency_ms": None,
        "status_code": None,
        "downstream_service": None,
    }


def test_windows_do_not_cross_ground_truth_intervals() -> None:
    windows = _windows_from_truth(manifest(), truth(), 30)
    assert [item.interval for item in windows] == ["baseline", "fault", "fault", "recovery"]
    assert [item.label for item in windows] == [
        "NORMAL",
        "DATABASE_HIGH_LATENCY",
        "DATABASE_HIGH_LATENCY",
        "NORMAL",
    ]


def test_feature_values_exclude_label_and_identifier_metadata() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    row = feature_record(
        _windows_from_truth(manifest(), truth(), 30)[0], [request(start)], [event(start)]
    )
    assert tuple(row["features"]) == FEATURE_NAMES
    assert "fault_id" not in row["features"]
    assert "run_id" not in row["features"]
    assert row["features"]["request_count"] == 1.0


def test_bucket_assigns_only_records_inside_a_complete_window() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    buckets, assigned = _bucket_records(
        [
            request(start),
            request(start + timedelta(seconds=31)),
            request(start + timedelta(seconds=121)),
        ],
        "started_at",
        _windows_from_truth(manifest(), truth(), 30),
    )
    assert assigned == 2
    assert [len(bucket) for bucket in buckets] == [1, 1, 0, 0]


def test_report_rejects_missing_window_matrix() -> None:
    assert "expected 10, 30 and 60-second feature outputs" in validate_feature_report(
        {"manifest_count": 280, "outputs": {}}
    )
