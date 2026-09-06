from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class FaultFamily(StrEnum):
    DATABASE_HIGH_LATENCY = "DATABASE_HIGH_LATENCY"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    DOWNSTREAM_TIMEOUT = "DOWNSTREAM_TIMEOUT"
    AUTHENTICATION_FAILURE_BURST = "AUTHENTICATION_FAILURE_BURST"
    HIGH_LOAD = "HIGH_LOAD"
    CONNECTION_POOL_EXHAUSTION = "CONNECTION_POOL_EXHAUSTION"
    DATABASE_LOCK_CONTENTION = "DATABASE_LOCK_CONTENTION"
    DOWNSTREAM_ERROR_BURST = "DOWNSTREAM_ERROR_BURST"


class FaultIntensity(StrEnum):
    MILD = "mild"
    MEDIUM = "medium"
    HIGH = "high"


class FaultDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fault_id: FaultFamily
    tier: Literal["core", "extended"]
    mechanism: Literal["database", "service", "downstream", "traffic"]
    allowed_targets: tuple[str, ...]
    allowed_intensities: tuple[FaultIntensity, ...] = tuple(FaultIntensity)
    description: str

    @model_validator(mode="after")
    def require_targets(self) -> FaultDefinition:
        if not self.allowed_targets:
            raise ValueError("a known fault must declare at least one target")
        return self


_APPLICATION_SERVICES = (
    "authentication",
    "account",
    "transaction",
    "payment",
    "ledger",
)


CORE_KNOWN_FAULTS = MappingProxyType(
    {
        FaultFamily.DATABASE_HIGH_LATENCY: FaultDefinition(
            fault_id=FaultFamily.DATABASE_HIGH_LATENCY, tier="core", mechanism="database",
            allowed_targets=_APPLICATION_SERVICES,
            description="Controlled delay on database work performed by one service.",
        ),
        FaultFamily.DATABASE_UNAVAILABLE: FaultDefinition(
            fault_id=FaultFamily.DATABASE_UNAVAILABLE, tier="core", mechanism="database",
            allowed_targets=("postgresql",),
            description="PostgreSQL becomes unreachable during a bounded window.",
        ),
        FaultFamily.SERVICE_UNAVAILABLE: FaultDefinition(
            fault_id=FaultFamily.SERVICE_UNAVAILABLE, tier="core", mechanism="service",
            allowed_targets=("account", "payment", "ledger"),
            description="A selected downstream application service is unavailable.",
        ),
        FaultFamily.DOWNSTREAM_TIMEOUT: FaultDefinition(
            fault_id=FaultFamily.DOWNSTREAM_TIMEOUT, tier="core", mechanism="downstream",
            allowed_targets=("transaction->account", "transaction->payment", "payment->ledger"),
            description="A selected dependency call exceeds its client timeout.",
        ),
        FaultFamily.AUTHENTICATION_FAILURE_BURST: FaultDefinition(
            fault_id=FaultFamily.AUTHENTICATION_FAILURE_BURST, tier="core", mechanism="traffic",
            allowed_targets=("authentication",),
            description="A burst of rejected logins beyond the normal-error mix.",
        ),
        FaultFamily.HIGH_LOAD: FaultDefinition(
            fault_id=FaultFamily.HIGH_LOAD, tier="core", mechanism="traffic",
            allowed_targets=("gateway",),
            description="Request load exceeds the calibrated high-healthy envelope.",
        ),
        FaultFamily.CONNECTION_POOL_EXHAUSTION: FaultDefinition(
            fault_id=FaultFamily.CONNECTION_POOL_EXHAUSTION, tier="core", mechanism="database",
            allowed_targets=_APPLICATION_SERVICES,
            description="Concurrent database work consumes a service connection pool.",
        ),
    }
)


EXTENDED_KNOWN_FAULTS = MappingProxyType(
    {
        FaultFamily.DATABASE_LOCK_CONTENTION: FaultDefinition(
            fault_id=FaultFamily.DATABASE_LOCK_CONTENTION, tier="extended", mechanism="database",
            allowed_targets=("transaction", "payment", "ledger"),
            description="A held database lock creates waits and bounded timeouts.",
        ),
        FaultFamily.DOWNSTREAM_ERROR_BURST: FaultDefinition(
            fault_id=FaultFamily.DOWNSTREAM_ERROR_BURST, tier="extended", mechanism="downstream",
            allowed_targets=("account", "payment", "ledger"),
            description="A live downstream service returns a controlled burst of errors.",
        ),
    }
)


# Identifiers only; implementations remain outside known-fault training code.
SEALED_UNKNOWN_FAULT_IDS = frozenset(
    {"INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE", "LATENCY_JITTER_PARTIAL_DEGRADATION"}
)


def get_known_fault(fault_id: FaultFamily | str) -> FaultDefinition:
    parsed = FaultFamily(fault_id)
    definition = CORE_KNOWN_FAULTS.get(parsed) or EXTENDED_KNOWN_FAULTS.get(parsed)
    if definition is None:
        raise ValueError(f"fault {parsed.value} is not registered as known")
    return definition


def validate_known_fault(
    fault_id: FaultFamily | str, target: str, intensity: FaultIntensity | str
) -> FaultDefinition:
    definition = get_known_fault(fault_id)
    parsed_intensity = FaultIntensity(intensity)
    if target not in definition.allowed_targets:
        choices = ", ".join(definition.allowed_targets)
        raise ValueError(f"target {target!r} is invalid; choose from {choices}")
    if parsed_intensity not in definition.allowed_intensities:
        raise ValueError(f"intensity {parsed_intensity.value!r} is not allowed")
    return definition
