from datetime import UTC, datetime

from datasets.incident_localization import (
    IntervalEvidence,
    rank_origins,
    validate_localization_report,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
EDGES = (
    ("gateway", "authentication"),
    ("gateway", "account"),
    ("gateway", "transaction"),
    ("transaction", "account"),
    ("transaction", "payment"),
    ("payment", "ledger"),
)


def event(
    service: str,
    downstream: str | None = None,
    *,
    success: bool = True,
    error_type: str | None = None,
    latency: float = 10.0,
    status_code: int | None = None,
    message: str = "test event",
) -> dict:
    if status_code is None:
        status_code = 200 if success else 503
    return {
        "timestamp": "2026-01-01T00:00:01Z",
        "service": service,
        "downstream_service": downstream,
        "success": success,
        "level": "INFO" if success else "ERROR",
        "status_code": status_code,
        "error_type": error_type,
        "event_type": "downstream_request_completed" if downstream else "service_event",
        "message": message,
        "latency_ms": latency,
    }


def evidence(*events: dict) -> IntervalEvidence:
    return IntervalEvidence(events=events, started_at=START)


def test_service_failure_localizes_downstream_service() -> None:
    baseline = evidence(event("gateway", "account"), event("account"))
    fault = evidence(
        event(
            "gateway",
            "account",
            success=False,
            error_type="ConnectError",
            message="account service unavailable",
        )
    )
    ranking = rank_origins(baseline, fault, EDGES)
    assert ranking[0]["candidate"] == "account"
    assert ranking[0]["reasons"]


def test_timeout_localizes_deepest_dependency_edge() -> None:
    baseline = evidence(
        event("gateway", "transaction"),
        event("transaction", "payment"),
    )
    fault = evidence(
        event(
            "gateway",
            "transaction",
            success=False,
            error_type="RequestTimeout",
            latency=5000,
            status_code=504,
        ),
        event(
            "transaction",
            "payment",
            success=False,
            error_type="RequestTimeout",
            latency=5000,
            status_code=504,
        ),
    )
    ranking = rank_origins(baseline, fault, EDGES)
    assert ranking[0]["candidate"] == "transaction->payment"
    assert ranking[0]["kind"] == "dependency_edge"


def test_pool_exhaustion_prefers_deepest_service_over_propagated_edge() -> None:
    baseline = evidence(
        event("gateway", "transaction"),
        event("transaction"),
        event("transaction", "payment"),
        event("payment"),
    )
    fault = evidence(
        event("gateway", "transaction", success=False, error_type="ConnectError", latency=3000),
        event("transaction", success=False, error_type="PoolTimeout", latency=3000),
        event("transaction", "payment", success=False, error_type="PoolTimeout", latency=3000),
        event("payment", success=False, error_type="PoolTimeout", latency=3000),
    )
    ranking = rank_origins(baseline, fault, EDGES)
    assert ranking[0]["candidate"] == "payment"


def test_database_refusal_can_localize_postgresql() -> None:
    baseline = evidence(event("authentication"), event("gateway", "authentication"))
    fault = evidence(
        event(
            "authentication",
            success=False,
            error_type="DatabaseConnectionError",
            latency=3000,
            message="postgres database connection refused",
        ),
        event(
            "gateway",
            "authentication",
            success=False,
            error_type="ConnectionRefused",
            latency=3000,
        ),
    )
    ranking = rank_origins(baseline, fault, EDGES)
    assert ranking[0]["candidate"] == "postgresql"



def test_service_unavailable_beats_timeout_propagation_when_connection_refused() -> None:
    baseline = evidence(event("transaction", "payment"), event("payment"))
    fault = evidence(
        event(
            "transaction",
            "payment",
            success=False,
            error_type="ConnectTimeout ConnectionRefused",
            latency=5000,
        )
    )
    ranking = rank_origins(baseline, fault, EDGES)
    assert ranking[0]["candidate"] == "payment"


def test_resource_contention_without_literal_pool_token_prefers_service() -> None:
    baseline = evidence(event("gateway", "transaction"), event("transaction"))
    fault = evidence(
        event(
            "gateway",
            "transaction",
            success=False,
            error_type="RequestTimeout",
            latency=5000,
        ),
        event(
            "transaction",
            success=False,
            error_type="RequestTimeout",
            latency=5000,
        ),
    )
    ranking = rank_origins(baseline, fault, EDGES)
    assert ranking[0]["candidate"] == "transaction"


def test_database_saturation_without_literal_db_token_prefers_postgresql() -> None:
    baseline = evidence(event("authentication"), event("gateway", "authentication"))
    fault = evidence(
        event(
            "authentication",
            success=False,
            error_type="RequestTimeout",
            latency=5000,
        ),
        event(
            "gateway",
            "authentication",
            success=False,
            error_type="RequestTimeout",
            latency=5000,
        ),
    )
    ranking = rank_origins(baseline, fault, EDGES)
    assert ranking[0]["candidate"] == "postgresql"

def test_validator_rejects_incomplete_report() -> None:
    errors = validate_localization_report(
        {
            "ground_truth_usage": "evaluation_only_after_ranking",
            "metrics": {
                "overall": {"runs": 1},
                "known_fault": {"runs": 1},
                "sealed_unknown": {"runs": 0},
            },
            "results": [],
        }
    )
    assert "expected localization results for 220 abnormal runs" in errors
    assert "localization result list is incomplete" in errors
