from datasets.isolation_forest_model import FeatureRow
from datasets.rule_baseline import fit_rules, triggered_rules


def test_rules_are_fitted_only_from_healthy_values() -> None:
    healthy = FeatureRow("a", "train", "normal", True, True, "NORMAL", "normal", (0.0,) * 33)
    rules = fit_rules([healthy])
    assert not triggered_rules(healthy, rules)
