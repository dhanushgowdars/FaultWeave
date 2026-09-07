from __future__ import annotations

import subprocess
from pathlib import Path

from experiments.faults.lifecycle import FaultActivation

PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
_SERVICE_NAMES = {
    "account": "account-service",
    "payment": "payment-service",
    "ledger": "ledger-service",
}


def compose(*arguments: str) -> None:
    result = subprocess.run(
        ["docker", "compose", *arguments], cwd=PROJECT_DIRECTORY,
        capture_output=True, check=False, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def activate_infrastructure_fault(activation: FaultActivation) -> None:
    if activation.fault_id.value == "DATABASE_UNAVAILABLE":
        compose("stop", "postgres")
    elif activation.fault_id.value == "SERVICE_UNAVAILABLE":
        compose("stop", _SERVICE_NAMES[activation.target])


def deactivate_infrastructure_fault(activation: FaultActivation) -> None:
    if activation.fault_id.value == "DATABASE_UNAVAILABLE":
        compose("up", "-d", "--wait", "postgres")
    elif activation.fault_id.value == "SERVICE_UNAVAILABLE":
        compose("up", "-d", "--wait", _SERVICE_NAMES[activation.target])


def verify_stack_recovery() -> None:
    compose("up", "-d", "--wait")
    result = subprocess.run(
        ["python", "scripts/smoke_test.py"], cwd=PROJECT_DIRECTORY,
        capture_output=True, check=False, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
