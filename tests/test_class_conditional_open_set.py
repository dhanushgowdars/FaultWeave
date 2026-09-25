from datasets.class_conditional_open_set import (
    OPEN_SET_LABEL,
    support_score,
    supported_label,
)


def test_support_score_uses_largest_standardized_deviation() -> None:
    profile = {"centers": [10.0, 100.0], "scales": [2.0, 10.0]}
    assert support_score((14.0, 110.0), profile) == 2.0


def test_supported_label_requires_anomaly_before_open_set_rejection() -> None:
    assert supported_label(0.1, 0.2, "KNOWN", 100.0, 2.0) == "NORMAL"
    assert supported_label(0.3, 0.2, "KNOWN", 3.0, 2.0) == OPEN_SET_LABEL
    assert supported_label(0.3, 0.2, "KNOWN", 1.0, 2.0) == "KNOWN"
