"""Reviewed fault scenarios that are valid for the durable transfer workflow."""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict

from experiments.faults.catalog import FaultFamily, FaultIntensity, validate_known_fault


class TransferFaultCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    title: str
    fault_id: FaultFamily
    target: str
    intensity: FaultIntensity
    expected_step: Literal["payment_execution", "account_commit", "ledger_posting"]
    expected_funds_status: Literal["UNKNOWN", "COMMITTED"]
    explanation: str
    operator_action: str


def _case(**values) -> TransferFaultCase:
    result = TransferFaultCase(**values)
    validate_known_fault(result.fault_id, result.target, result.intensity)
    return result


TRANSFER_FAULT_CASES = MappingProxyType(
    {
        "payment-service-unavailable": _case(
            case_id="payment-service-unavailable",
            title="Payment service unavailable",
            fault_id=FaultFamily.SERVICE_UNAVAILABLE,
            target="payment",
            intensity=FaultIntensity.HIGH,
            expected_step="payment_execution",
            expected_funds_status="UNKNOWN",
            explanation="Transaction cannot confirm whether Payment received the instruction.",
            operator_action="Restore Payment, inspect the case, then resume the same transfer.",
        ),
        "ledger-service-unavailable": _case(
            case_id="ledger-service-unavailable",
            title="Ledger unavailable after funds commit",
            fault_id=FaultFamily.SERVICE_UNAVAILABLE,
            target="ledger",
            intensity=FaultIntensity.HIGH,
            expected_step="ledger_posting",
            expected_funds_status="COMMITTED",
            explanation="Account committed both entries, but Payment lacks ledger confirmation.",
            operator_action="Restore Ledger and resume without moving funds again.",
        ),
        "account-response-timeout": _case(
            case_id="account-response-timeout",
            title="Account dependency timeout",
            fault_id=FaultFamily.DOWNSTREAM_TIMEOUT,
            target="payment->account",
            intensity=FaultIntensity.MEDIUM,
            expected_step="account_commit",
            expected_funds_status="UNKNOWN",
            explanation="Payment did not receive an Account result and must reconcile by key.",
            operator_action="Inspect Account evidence and resume the same transfer after cleanup.",
        ),
        "ledger-response-timeout": _case(
            case_id="ledger-response-timeout",
            title="Ledger confirmation timeout",
            fault_id=FaultFamily.DOWNSTREAM_TIMEOUT,
            target="payment->ledger",
            intensity=FaultIntensity.MEDIUM,
            expected_step="ledger_posting",
            expected_funds_status="COMMITTED",
            explanation="Funds are committed while ledger confirmation remains unavailable.",
            operator_action="Reconcile the journal and resume the original ledger posting.",
        ),
        "account-error-response": _case(
            case_id="account-error-response",
            title="Account returns a controlled server error",
            fault_id=FaultFamily.DOWNSTREAM_ERROR_BURST,
            target="account",
            intensity=FaultIntensity.HIGH,
            expected_step="account_commit",
            expected_funds_status="UNKNOWN",
            explanation="Account returned an error before Payment obtained commit evidence.",
            operator_action="Inspect Account logs, clear the fault, and resume by idempotency key.",
        ),
    }
)


def get_transfer_fault_case(case_id: str) -> TransferFaultCase:
    try:
        return TRANSFER_FAULT_CASES[case_id]
    except KeyError as exc:
        choices = ", ".join(TRANSFER_FAULT_CASES)
        raise ValueError(f"unknown transfer fault case {case_id!r}; choose from {choices}") from exc
