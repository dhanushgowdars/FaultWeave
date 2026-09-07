import pytest

from experiments.faults.catalog import get_known_fault
from experiments.sealed_unknowns.catalog import (
    SEALED_UNKNOWN_SCENARIOS,
    SealedUnknownFamily,
    validate_unknown_scenario,
)


def test_exact_frozen_unknown_registry_is_evaluation_only() -> None:
    assert {item.value for item in SEALED_UNKNOWN_SCENARIOS} == {
        "INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE",
        "LATENCY_JITTER_PARTIAL_DEGRADATION",
    }
    assert all(item.evaluation_only for item in SEALED_UNKNOWN_SCENARIOS.values())


def test_unknown_scenario_targets_are_validated() -> None:
    definition = validate_unknown_scenario(
        SealedUnknownFamily.INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE,
        "transaction->payment",
        "medium",
    )
    assert definition.evaluation_only
    with pytest.raises(ValueError, match="target"):
        validate_unknown_scenario(
            SealedUnknownFamily.INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE,
            "postgresql",
            "medium",
        )


@pytest.mark.parametrize("identifier", [item.value for item in SealedUnknownFamily])
def test_unknown_scenarios_are_not_known_classifier_classes(identifier: str) -> None:
    with pytest.raises(ValueError):
        get_known_fault(identifier)
