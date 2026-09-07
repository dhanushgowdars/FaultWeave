from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from experiments.faults.catalog import FaultFamily, FaultIntensity, validate_known_fault


def utc_now() -> datetime:
    return datetime.now(UTC)


class FaultActivation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    activation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,75}$")
    fault_id: FaultFamily
    target: str
    intensity: FaultIntensity
    duration_seconds: float = Field(gt=0, le=300)
    activated_at: datetime
    expires_at: datetime


class FaultStateStore:
    """Exclusive, process-safe lease for one controlled fault at a time."""

    def __init__(self, control_directory: Path) -> None:
        self.control_directory = control_directory
        self.active_path = control_directory / "active_fault.json"

    def active(self) -> FaultActivation | None:
        try:
            payload = json.loads(self.active_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if payload.get("experiment_scope") == "sealed_unknown_evaluation":
            raise RuntimeError("a sealed-unknown evaluation owns the global fault lease")
        activation = FaultActivation.model_validate(payload)
        if activation.expires_at <= utc_now():
            self.release(activation.activation_id)
            return None
        return activation

    def acquire(
        self,
        *,
        run_id: str,
        fault_id: FaultFamily | str,
        target: str,
        intensity: FaultIntensity | str,
        duration_seconds: float,
    ) -> FaultActivation:
        validate_known_fault(fault_id, target, intensity)
        now = utc_now()
        activation = FaultActivation(
            activation_id=uuid4().hex,
            run_id=run_id,
            fault_id=FaultFamily(fault_id),
            target=target,
            intensity=FaultIntensity(intensity),
            duration_seconds=duration_seconds,
            activated_at=now,
            expires_at=now + timedelta(seconds=duration_seconds),
        )
        self.control_directory.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.active_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError as exc:
            active = self.active()
            if active is None:
                return self.acquire(
                    run_id=run_id,
                    fault_id=fault_id,
                    target=target,
                    intensity=intensity,
                    duration_seconds=duration_seconds,
                )
            raise RuntimeError(
                f"fault {active.activation_id} is already active for run {active.run_id}"
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(activation.model_dump_json(indent=2))
            stream.write("\n")
        return activation

    def release(self, activation_id: str) -> None:
        try:
            current = FaultActivation.model_validate_json(
                self.active_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return
        if current.activation_id != activation_id:
            raise RuntimeError("refusing to release a fault owned by another activation")
        self.active_path.unlink(missing_ok=True)


class FaultLease(AbstractContextManager[FaultActivation]):
    """Runs activation hooks and guarantees cleanup and recovery checks."""

    def __init__(
        self,
        store: FaultStateStore,
        activation: FaultActivation,
        activate: Callable[[FaultActivation], None],
        deactivate: Callable[[FaultActivation], None],
        verify_recovery: Callable[[], None],
    ) -> None:
        self.store = store
        self.activation = activation
        self.activate_hook = activate
        self.deactivate_hook = deactivate
        self.verify_recovery_hook = verify_recovery
        self._activated = False

    def __enter__(self) -> FaultActivation:
        try:
            self.activate_hook(self.activation)
            self._activated = True
            return self.activation
        except BaseException:
            self.store.release(self.activation.activation_id)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> Literal[False]:
        cleanup_error: BaseException | None = None
        try:
            if self._activated:
                self.deactivate_hook(self.activation)
            self.verify_recovery_hook()
        except BaseException as exc:
            cleanup_error = exc
        finally:
            self.store.release(self.activation.activation_id)
        if cleanup_error is not None:
            raise RuntimeError("fault cleanup or recovery verification failed") from cleanup_error
        return False
