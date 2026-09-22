from datasets.isolation_forest_model import FeatureRow
from datasets.xgboost_classifier import known_fault_rows


def test_known_fault_training_excludes_normal_and_sealed_rows() -> None:
    rows = [
        FeatureRow(
            "a", "train", "known_fault", True, True, "DATABASE_HIGH_LATENCY", "fault", (1.0,)
        ),
        FeatureRow("b", "train", "normal", True, True, "NORMAL", "normal", (1.0,)),
    ]
    selected = known_fault_rows(rows, "train")
    assert len(selected) == 1
    assert selected[0].label == "DATABASE_HIGH_LATENCY"
