from datasets.quality_profile import missing_fields, validate_profile


def valid_profile() -> dict[str, object]:
    return {
        "manifest_count": 55,
        "scenario_counts": {"known_fault": 45, "normal": 10},
        "known_fault_counts": {f"FAULT_{index}": 5 for index in range(9)},
        "request_missing_fields": {},
        "event_missing_fields": {},
        "correlation_complete_runs": 55,
        "interval_timings_valid": True,
        "sealed_unknown_included": False,
    }


def test_complete_profile_is_accepted() -> None:
    assert validate_profile(valid_profile()) == []


def test_profile_rejects_unknown_contamination_and_missing_correlation() -> None:
    report = valid_profile()
    report["sealed_unknown_included"] = True
    report["correlation_complete_runs"] = 54
    errors = validate_profile(report)
    assert any("sealed unknown" in error for error in errors)
    assert any("correlation" in error for error in errors)


def test_nullable_transport_status_is_not_a_missing_observable_field() -> None:
    assert missing_fields([{"status_code": None}], frozenset({"status_code"})) == {}
