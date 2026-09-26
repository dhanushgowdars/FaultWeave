from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.intelligence_incidents import severity_from_feature_windows
from app.intelligence_localization import (
    LiveIntervalEvidence,
    TOPOLOGY_EDGES,
    rank_live_origins,
)
from datasets.incident_impact import severity_from_windows
from datasets.incident_localization import IntervalEvidence, rank_origins


def _feature_row(
    *,
    failure: float,
    request_latency: float,
    event_latency: float,
    auth_failure: float,
    auth_latency: float,
) -> dict[str, float]:
    return {
        "request_unexpected_outcome_rate": failure,
        "request_server_error_rate": 0.0,
        "request_transport_error_rate": 0.0,
        "event_failure_rate": failure,
        "event_dependency_failure_rate": failure,
        "request_latency_p95_ms": request_latency,
        "event_latency_p95_ms": event_latency,
        "event_service_authentication_failure_rate": auth_failure,
        "event_service_authentication_latency_p95_ms": auth_latency,
    }


def test_live_severity_preserves_phase13_formula() -> None:
    baseline = [
        _feature_row(
            failure=0.0,
            request_latency=100.0,
            event_latency=80.0,
            auth_failure=0.0,
            auth_latency=40.0,
        )
        for _ in range(3)
    ]
    current = [
        _feature_row(
            failure=0.6,
            request_latency=500.0,
            event_latency=420.0,
            auth_failure=0.8,
            auth_latency=200.0,
        )
        for _ in range(6)
    ]
    flags = [True, True, True, True, True, False]

    live = severity_from_feature_windows(baseline, current, flags)
    offline = severity_from_windows(
        [{"features": row} for row in baseline],
        [{"features": row} for row in current],
        flags,
    )

    assert live == offline


def _event(
    at: datetime,
    *,
    service: str,
    downstream: str | None = None,
    success: bool = True,
    status: int = 200,
    latency: float = 20.0,
    error_type: str | None = None,
    event_type: str = "downstream_request_completed",
    message: str = "request completed",
) -> dict[str, object]:
    return {
        "timestamp": at.isoformat(),
        "service": service,
        "downstream_service": downstream,
        "success": success,
        "level": "INFO" if success else "ERROR",
        "status_code": status,
        "latency_ms": latency,
        "error_type": error_type,
        "event_type": event_type,
        "message": message,
    }


def test_live_localization_preserves_phase12c_ranking() -> None:
    start = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    baseline_events = tuple(
        _event(
            start + timedelta(seconds=index),
            service="gateway",
            downstream="authentication",
        )
        for index in range(10)
    ) + tuple(
        _event(
            start + timedelta(seconds=index),
            service="authentication",
            downstream=None,
        )
        for index in range(10)
    )
    current_start = start + timedelta(minutes=1)
    current_events = tuple(
        _event(
            current_start + timedelta(seconds=index),
            service="gateway",
            downstream="authentication",
            success=False,
            status=401,
            latency=60.0,
            error_type="InvalidCredentials",
        )
        for index in range(10)
    ) + tuple(
        _event(
            current_start + timedelta(seconds=index),
            service="authentication",
            downstream=None,
            success=False,
            status=401,
            latency=50.0,
            error_type="InvalidCredentials",
            event_type="authentication_failed",
            message="Authentication attempt was rejected",
        )
        for index in range(10)
    )

    live = rank_live_origins(
        LiveIntervalEvidence(baseline_events, start),
        LiveIntervalEvidence(current_events, current_start),
    )
    offline = rank_origins(
        IntervalEvidence(baseline_events, start),
        IntervalEvidence(current_events, current_start),
        TOPOLOGY_EDGES,
    )

    assert live == offline
