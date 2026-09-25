from datasets.open_set_loko import validate_loko_artifacts


def test_verifier_rejects_missing_artifacts(tmp_path) -> None:
    assert validate_loko_artifacts(tmp_path)
