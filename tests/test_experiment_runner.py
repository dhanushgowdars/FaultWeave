from experiments.runner import build_plan, percentile, validate_correlated_run
from experiments.traffic_profiles import get_profile


def test_seeded_plan_is_reproducible() -> None:
    profile = get_profile("normal_errors")
    first = build_plan(profile, seed=35001, duration_seconds=10, target_rps=5)
    second = build_plan(profile, seed=35001, duration_seconds=10, target_rps=5)
    different = build_plan(profile, seed=35002, duration_seconds=10, target_rps=5)
    assert first == second
    assert first != different


def test_percentile_interpolates_ordered_values() -> None:
    assert percentile([40.0, 10.0, 30.0, 20.0], 0.5) == 25.0
    assert percentile([7.0], 0.95) == 7.0


def test_valid_request_requires_frozen_services_edges_and_trace() -> None:
    request = {
        "request_id": "request-1",
        "trace_id": "trace-1",
        "scenario": "valid",
    }
    services = {"gateway", "authentication", "account", "transaction", "payment", "ledger"}
    edges = {
        ("gateway", "authentication"),
        ("gateway", "account"),
        ("gateway", "transaction"),
        ("transaction", "account"),
        ("transaction", "payment"),
        ("transaction", "ledger"),
        ("payment", "ledger"),
    }
    events = [
        {
            "request_id": "request-1",
            "trace_id": "trace-1",
            "service": service,
            "event_type": "http_request_completed",
            "success": True,
        }
        for service in services
    ]
    events.extend(
        {
            "request_id": "request-1",
            "trace_id": "trace-1",
            "service": source,
            "downstream_service": target,
            "event_type": "downstream_request_completed",
            "success": True,
        }
        for source, target in edges
    )
    assert validate_correlated_run([request], events) == []


def test_expected_user_error_does_not_require_full_service_path() -> None:
    request = {
        "request_id": "request-2",
        "trace_id": "trace-2",
        "scenario": "invalid_amount",
    }
    events = [
        {
            "request_id": "request-2",
            "trace_id": "trace-2",
            "service": "gateway",
            "event_type": "http_request_completed",
            "success": False,
        }
    ]
    assert validate_correlated_run([request], events) == []
