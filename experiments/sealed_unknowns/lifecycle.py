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

from experiments.faults.catalog import FaultIntensity

from .catalog import SealedUnknownFamily, validate_unknown_scenario


class SealedUnknownActivation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    experiment_scope: Literal["sealed_unknown_evaluation"] = "sealed_unknown_evaluation"
    activation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,75}$")
    fault_id: SealedUnknownFamily
    target: str
    intensity: FaultIntensity
    duration_seconds: float = Field(gt=0, le=300)
    activated_at: datetime
    expires_at: datetime


class SealedUnknownStateStore:
    """Uses the global fault lease file so known and unknown faults cannot overlap."""

    def __init__(self, control_directory: Path) -> None:
        self.control_directory = control_directory
        self.active_path = control_directory / "active_fault.json"

    def active(self) -> SealedUnknownActivation | None:
        try:
            payload = json.loads(self.active_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if payload.get("experiment_scope") != "sealed_unknown_evaluation":
            raise RuntimeError("a known-fault activation already owns the global lease")
        activation = SealedUnknownActivation.model_validate(payload)
        if activation.expires_at <= datetime.now(UTC):
            self.release(activation.activation_id)
            return None
        return activation

    def acquire(
        self,
        *,
        run_id: str,
        fault_id: SealedUnknownFamily | str,
        target: str,
        intensity: FaultIntensity | str,
        duration_seconds: float,
    ) -> SealedUnknownActivation:
        validate_unknown_scenario(fault_id, target, intensity)
        now = datetime.now(UTC)
        activation = SealedUnknownActivation(
            activation_id=uuid4().hex,
            run_id=run_id,
            fault_id=SealedUnknownFamily(fault_id),
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
            raise RuntimeError("another controlled fault already owns the global lease") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(activation.model_dump_json(indent=2))
            stream.write("\n")
        return activation

    def release(self, activation_id: str) -> None:
        try:
            payload = json.loads(self.active_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if payload.get("activation_id") != activation_id:
            raise RuntimeError("refusing to release an activation owned by another experiment")
        self.active_path.unlink(missing_ok=True)


class SealedUnknownLease(AbstractContextManager[SealedUnknownActivation]):
    def __init__(
        self,
        store: SealedUnknownStateStore,
        activation: SealedUnknownActivation,
        verify_recovery: Callable[[], None],
    ) -> None:
        self.store = store
        self.activation = activation
        self.verify_recovery = verify_recovery

    def __enter__(self) -> SealedUnknownActivation:
        return self.activation

    def __exit__(self, exc_type, exc_value, traceback) -> Literal[False]:
        recovery_error: BaseException | None = None
        try:
            self.store.release(self.activation.activation_id)
            self.verify_recovery()
        except BaseException as exc:
            recovery_error = exc
        finally:
            self.store.release(self.activation.activation_id)
        if recovery_error is not None:
            message = "unknown-scenario cleanup or recovery verification failed"
            raise RuntimeError(message) from recovery_error
        return False
