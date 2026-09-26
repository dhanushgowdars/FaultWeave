from datetime import UTC, datetime

from datasets.incident_impact import (
    detection_delay_seconds,
    severity_band,
    severity_from_windows,
)
from datasets.final_features import FEATURE_NAMES


def row(*, ended_at: str = "2026-01-01T00:00:10Z", **updates: float) -> dict:
    features = {name: 0.0 for name in FEATURE_NAMES}
    features.update(updates)
    return {"window_ended_at": ended_at, "features": features}


def test_detection_delay_uses_window_end_as_confirmation_time() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        row(ended_at="2026-01-01T00:00:10Z"),
        row(ended_at="2026-01-01T00:00:20Z"),
    ]
    assert detection_delay_seconds(start, rows, [False, True]) == 20.0
    assert detection_delay_seconds(start, rows, [False, False]) is None


def test_severity_band_boundaries_are_stable() -> None:
    assert severity_band(0.0) == "LOW"
    assert severity_band(0.249) == "LOW"
    assert severity_band(0.25) == "MEDIUM"
    assert severity_band(0.50) == "HIGH"
    assert severity_band(0.75) == "CRITICAL"


def test_severity_uses_observable_impact_components() -> None:
    baseline = [
        row(
            request_latency_p95_ms=100,
            event_latency_p95_ms=100,
            event_service_gateway_latency_p95_ms=100,
        )
    ]
    fault = [
        row(
            request_unexpected_outcome_rate=1.0,
            event_failure_rate=1.0,
            request_latency_p95_ms=1000,
            event_latency_p95_ms=1000,
            event_service_gateway_failure_rate=1.0,
            event_service_gateway_latency_p95_ms=1000,
        )
    ]
    result = severity_from_windows(baseline, fault, [True])
    assert 0.0 <= result["score"] <= 1.0
    assert result["level"] in {"HIGH", "CRITICAL"}
    assert "gateway" in result["affected_services"]
    assert result["components"]["anomaly_persistence"] == 1.0


def test_low_impact_healthy_like_window_stays_low() -> None:
    baseline = [row(request_latency_p95_ms=100, event_latency_p95_ms=100)]
    fault = [row(request_latency_p95_ms=105, event_latency_p95_ms=105)]
    result = severity_from_windows(baseline, fault, [False])
    assert result["level"] == "LOW"
