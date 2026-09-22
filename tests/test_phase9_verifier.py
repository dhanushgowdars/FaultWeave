from datasets.xgboost_classifier import validate_xgboost_artifacts


def test_verifier_rejects_missing_classifier_artifacts(tmp_path) -> None:
    assert validate_xgboost_artifacts(tmp_path)
