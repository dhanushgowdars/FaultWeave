from datasets.open_set_rejection import validate_open_set_artifacts


def test_verifier_rejects_missing_artifacts(tmp_path) -> None:
    assert validate_open_set_artifacts(tmp_path)
