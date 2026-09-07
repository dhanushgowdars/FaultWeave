from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from experiments.faults.catalog import SEALED_UNKNOWN_FAULT_IDS, FaultIntensity


class SealedUnknownFamily(StrEnum):
    INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE = (
        "INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE"
    )
    LATENCY_JITTER_PARTIAL_DEGRADATION = "LATENCY_JITTER_PARTIAL_DEGRADATION"


class SealedUnknownDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fault_id: SealedUnknownFamily
    allowed_targets: tuple[str, ...]
    evaluation_only: bool = True
    description: str


SEALED_UNKNOWN_SCENARIOS = MappingProxyType(
    {
        SealedUnknownFamily.INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE:
            SealedUnknownDefinition(
                fault_id=SealedUnknownFamily.INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE,
                allowed_targets=(
                    "gateway->authentication",
                    "gateway->account",
                    "gateway->transaction",
                    "transaction->account",
                    "transaction->payment",
                    "transaction->ledger",
                    "payment->ledger",
                ),
                description="Intermittent connection loss on one dependency edge.",
            ),
        SealedUnknownFamily.LATENCY_JITTER_PARTIAL_DEGRADATION:
            SealedUnknownDefinition(
                fault_id=SealedUnknownFamily.LATENCY_JITTER_PARTIAL_DEGRADATION,
                allowed_targets=(
                    "authentication", "account", "transaction", "payment", "ledger"
                ),
                description="Multi-modal request latency affecting only part of the traffic.",
            ),
    }
)


if {item.value for item in SEALED_UNKNOWN_SCENARIOS} != SEALED_UNKNOWN_FAULT_IDS:
    raise RuntimeError("sealed unknown implementation differs from the frozen identifiers")


def validate_unknown_scenario(
    fault_id: SealedUnknownFamily | str,
    target: str,
    intensity: FaultIntensity | str,
) -> SealedUnknownDefinition:
    parsed = SealedUnknownFamily(fault_id)
    definition = SEALED_UNKNOWN_SCENARIOS[parsed]
    FaultIntensity(intensity)
    if target not in definition.allowed_targets:
        choices = ", ".join(definition.allowed_targets)
        raise ValueError(f"target {target!r} is invalid; choose from {choices}")
    return definition
