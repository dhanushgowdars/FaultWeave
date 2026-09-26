from datasets.incident_impact import SEVERITY_WEIGHTS, validate_incident_impact_report


def test_phase13_validator_accepts_complete_contract() -> None:
    results = [
        {
            "run_id": f"run-{index}",
            "detected": True,
            "detection_delay_seconds": 10.0,
            "severity": {"score": 0.4, "level": "MEDIUM"},
        }
        for index in range(220)
    ]
    report = {
        "severity_weights": SEVERITY_WEIGHTS,
        "temporal_detector": {"window_seconds": 10, "development_unknown_tuning_rows": 0},
        "metrics": {
            "overall": {"runs": 220},
            "known_fault": {"runs": 180},
            "development_unknown": {"runs": 40},
        },
        "results": results,
    }
    assert validate_incident_impact_report(report) == []


def test_phase13_validator_rejects_unknown_tuning_leakage() -> None:
    report = {
        "severity_weights": SEVERITY_WEIGHTS,
        "temporal_detector": {"window_seconds": 10, "development_unknown_tuning_rows": 1},
        "metrics": {
            "overall": {"runs": 0},
            "known_fault": {"runs": 0},
            "development_unknown": {"runs": 0},
        },
        "results": [],
    }
    errors = validate_incident_impact_report(report)
    assert "development unknowns leaked into temporal tuning" in errors
