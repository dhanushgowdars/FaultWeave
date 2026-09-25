from datasets.incident_localization import validate_localization_report


def test_phase12_contract_accepts_complete_evidence_rows() -> None:
    results = [
        {
            "run_id": f"run-{index}",
            "top_3": ["gateway", "authentication", "account"],
            "ranking": [{"candidate": "gateway", "score": 1.0}],
        }
        for index in range(220)
    ]
    report = {
        "ground_truth_usage": "evaluation_only_after_ranking",
        "metrics": {
            "overall": {"runs": 220},
            "known_fault": {"runs": 180},
            "sealed_unknown": {"runs": 40},
        },
        "results": results,
    }
    assert validate_localization_report(report) == []
