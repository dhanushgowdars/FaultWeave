from datasets.open_set_rejection import OPEN_SET_LABEL, confidence_threshold, final_label


def test_open_set_gate_preserves_normal_and_rejects_low_confidence_anomalies() -> None:
    assert final_label(False, "DATABASE_HIGH_LATENCY", 0.99, 0.8, 5.0, 6.0) == "NORMAL"
    assert final_label(True, "DATABASE_HIGH_LATENCY", 0.79, 0.8, 5.0, 6.0) == OPEN_SET_LABEL
    assert final_label(True, "DATABASE_HIGH_LATENCY", 0.9, 0.8, 6.1, 6.0) == OPEN_SET_LABEL
    assert final_label(True, "DATABASE_HIGH_LATENCY", 0.9, 0.8, 5.0, 6.0) == "DATABASE_HIGH_LATENCY"


def test_confidence_threshold_uses_known_validation_values() -> None:
    assert confidence_threshold([0.1, 0.5, 0.9]) == 0.1
