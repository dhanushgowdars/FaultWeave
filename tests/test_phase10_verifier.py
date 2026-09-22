from datasets.rule_baseline import validate_rule_baseline_artifacts


def test_verifier_rejects_missing_artifacts(tmp_path) -> None:
    assert validate_rule_baseline_artifacts(tmp_path)
