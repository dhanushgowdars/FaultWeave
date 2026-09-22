from datasets.isolation_forest_model import (
    FeatureRow,
    choose_threshold,
    normal_training_rows,
    validation_rows,
)


def row(split: str, label: str, scenario: str = "normal") -> FeatureRow:
    return FeatureRow("run", split, scenario, True, True, label, "normal", (1.0, 2.0))


def test_training_uses_only_eligible_normal_train_rows() -> None:
    rows = [row("train", "NORMAL"), row("train", "DATABASE_HIGH_LATENCY", "known_fault")]
    selected = normal_training_rows(rows)
    assert len(selected) == 1
    assert selected[0].label == "NORMAL"


def test_validation_excludes_sealed_unknown_rows() -> None:
    rows = [row("validation", "NORMAL"), row("validation", "DATABASE_HIGH_LATENCY", "known_fault")]
    assert len(validation_rows(rows)) == 2


def test_threshold_optimizes_known_validation_labels() -> None:
    threshold, metrics = choose_threshold([0.01, 0.02, 0.8, 0.9], [False, False, True, True])
    assert 0.02 < threshold <= 0.8
    assert metrics["validation_f1"] == 1.0
