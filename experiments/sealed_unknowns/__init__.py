"""Evaluation-only unknown anomaly scenarios.

This package must never be imported by training, calibration or feature-selection code.
"""

from .catalog import SEALED_UNKNOWN_SCENARIOS, SealedUnknownFamily, validate_unknown_scenario
from .lifecycle import SealedUnknownActivation, SealedUnknownLease, SealedUnknownStateStore

__all__ = [
    "SEALED_UNKNOWN_SCENARIOS",
    "SealedUnknownActivation",
    "SealedUnknownFamily",
    "SealedUnknownLease",
    "SealedUnknownStateStore",
    "validate_unknown_scenario",
]
