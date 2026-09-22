from datasets.isolation_forest_model import validate_isolation_forest_artifacts


def test_verifier_rejects_missing_artifacts(tmp_path) -> None:
    errors = validate_isolation_forest_artifacts(tmp_path)
    assert errors
