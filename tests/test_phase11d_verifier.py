from pathlib import Path

from datasets.class_conditional_open_set import validate_class_conditional_artifacts


def test_verifier_rejects_missing_artifacts(tmp_path: Path) -> None:
    assert validate_class_conditional_artifacts(tmp_path)
