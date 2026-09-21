from datasets.final_quality_profile import (
    measurable_fault_signal,
    percentile,
    recovery_is_healthy,
    validate_final_profile,
)


def interval(
    *, non_2xx: float = 0.0, transport: float = 0.0, unexpected: float = 0.0, p95: float = 100.0
) -> dict[str, object]:
    return {
        "request_count": 20,
        "non_2xx_rate": non_2xx,
        "transport_error_rate": transport,
        "unexpected_outcome_rate": unexpected,
        "latency_ms": {"p95": p95},
    }


def valid_report() -> dict[str, object]:
    return {
        "manifest_count": 280,
        "scenario_counts": {"known_fault": 180, "normal": 60, "sealed_unknown": 40},
        "split_counts": {
            "evaluation_only": 40,
            "test": 48,
            "train": 144,
            "validation": 48,
        },
        "known_fault_counts": {f"FAULT_{index}": 20 for index in range(9)},
        "sealed_unknown_counts": {f"UNKNOWN_{index}": 20 for index in range(2)},
        "request_missing_fields": {},
        "event_missing_fields": {},
        "correlation_complete_runs": 280,
        "request_interval_assignment_rate": 1.0,
        "event_interval_assignment_rate": 1.0,
        "fault_signal_runs": 220,
        "fault_run_count": 220,
        "recovery_healthy_runs": 220,
        "service_counts": {f"service-{index}": 1 for index in range(6)},
        "event_type_counts": {f"event-{index}": 1 for index in range(5)},
        "request_count": 1,
        "event_count": 1,
    }


def test_percentile_interpolates_values() -> None:
    assert percentile([10.0, 20.0, 30.0], 0.50) == 20.0
    assert percentile([], 0.95) is None


def test_latency_change_is_a_measurable_fault_signal() -> None:
    detected, reasons = measurable_fault_signal(
        {"baseline": interval(p95=100), "fault": interval(p95=250)}
    )
    assert detected
    assert "p95_latency_increased" in reasons


def test_authentication_failure_status_is_a_measurable_signal() -> None:
    detected, reasons = measurable_fault_signal(
        {"baseline": interval(), "fault": interval(non_2xx=1.0)}
    )
    assert detected
    assert "non_2xx_rate_increased" in reasons


def test_recovery_requires_clean_requests() -> None:
    assert recovery_is_healthy({"recovery": interval()})
    assert not recovery_is_healthy({"recovery": interval(transport=0.1)})


def test_complete_final_profile_is_accepted() -> None:
    assert validate_final_profile(valid_report()) == []


def test_profile_rejects_missing_signal_and_interval_assignment() -> None:
    report = valid_report()
    report["fault_signal_runs"] = 219
    report["event_interval_assignment_rate"] = 0.98
    errors = validate_final_profile(report)
    assert any("observable signal" in error for error in errors)
    assert any("events map" in error for error in errors)
