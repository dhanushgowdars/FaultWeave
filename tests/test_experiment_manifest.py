import json
from datetime import UTC, datetime

from experiments.manifest import (
    ExperimentManifest,
    LatencySummary,
    RunArtifacts,
    RunSummary,
    sha256_file,
)


def test_manifest_round_trip_and_artifact_checksum(tmp_path) -> None:
    artifact = tmp_path / "events.jsonl"
    artifact.write_text('{"event":"ok"}\n', encoding="utf-8")
    checksum = sha256_file(artifact)
    now = datetime.now(UTC)
    manifest = ExperimentManifest(
        run_id="normal-low-test",
        profile="low",
        seed=31001,
        configured_duration_seconds=1,
        actual_duration_seconds=1,
        target_rps=3,
        rate_segments=[{"fraction": 1.0, "multiplier": 1.0}],
        started_at=now,
        ended_at=now,
        gateway_url="http://localhost:18110",
        git_commit=None,
        summary=RunSummary(
            scheduled_requests=1,
            completed_responses=1,
            expected_outcomes=1,
            completed_transactions=1,
            unexpected_outcomes=0,
            transport_errors=0,
            server_errors=0,
            response_rate=1,
            success_rate=1,
            expected_outcome_rate=1,
            completed_transaction_rate=1,
            achieved_rps=1,
            status_counts=[{"status_code": 200, "count": 1}],
            scenario_counts={"valid": 1},
            latency=LatencySummary(
                minimum_ms=1,
                p50_ms=1,
                p95_ms=1,
                p99_ms=1,
                maximum_ms=1,
            ),
            correlated_event_count=1,
        ),
        artifacts=RunArtifacts(
            requests_path="requests.jsonl",
            events_path="events.jsonl",
            requests_sha256=checksum,
            events_sha256=checksum,
        ),
    )
    restored = ExperimentManifest.model_validate_json(
        json.dumps(manifest.model_dump(mode="json"))
    )
    assert restored.run_type == "normal"
    assert restored.fault_injection_enabled is False
    assert restored.artifacts.events_sha256 == checksum
