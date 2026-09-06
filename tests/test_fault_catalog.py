import pytest

from experiments.faults.catalog import (
    CORE_KNOWN_FAULTS,
    EXTENDED_KNOWN_FAULTS,
    SEALED_UNKNOWN_FAULT_IDS,
    FaultFamily,
    get_known_fault,
    validate_known_fault,
)


def test_frozen_fault_family_counts_and_separation() -> None:
    assert len(CORE_KNOWN_FAULTS) == 7
    assert len(EXTENDED_KNOWN_FAULTS) == 2
    known_ids = {item.value for item in (*CORE_KNOWN_FAULTS, *EXTENDED_KNOWN_FAULTS)}
    assert known_ids.isdisjoint(SEALED_UNKNOWN_FAULT_IDS)


def test_targets_are_validated_per_fault_family() -> None:
    definition = validate_known_fault(
        FaultFamily.DOWNSTREAM_TIMEOUT, "transaction->payment", "medium"
    )
    assert definition.fault_id is FaultFamily.DOWNSTREAM_TIMEOUT
    with pytest.raises(ValueError, match="target"):
        validate_known_fault(FaultFamily.DOWNSTREAM_TIMEOUT, "postgresql", "medium")


def test_unknown_identifiers_cannot_be_loaded_as_known_faults() -> None:
    with pytest.raises(ValueError):
        get_known_fault("LATENCY_JITTER_PARTIAL_DEGRADATION")
