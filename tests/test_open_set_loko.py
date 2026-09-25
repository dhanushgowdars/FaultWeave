from datasets.open_set_loko import _choose_policy


def test_policy_prefers_rejection_of_simulated_unseen_class() -> None:
    held_out = {
        "anomaly_score": 0.8,
        "confidence": 0.2,
        "novelty_distance": 5.0,
        "is_held_out_class": True,
    }
    retained = {
        "anomaly_score": 0.9,
        "confidence": 0.9,
        "novelty_distance": 0.1,
        "is_held_out_class": False,
    }
    thresholds, metrics = _choose_policy([held_out, retained])
    assert thresholds["confidence_threshold"] > 0.2
    assert thresholds["anomaly_threshold"] <= 0.8
    assert metrics["synthetic_unknown_recall"] == 1.0
