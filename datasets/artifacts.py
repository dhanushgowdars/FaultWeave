from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DatasetArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    record_count: int = Field(ge=1)


class SmokeRunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    dataset_id: Literal["smoke-dataset-v1"] = "smoke-dataset-v1"
    run_id: str
    attempt_id: str = Field(pattern=r"^[a-z0-9-]{1,75}$")
    scenario_type: Literal["normal", "known_fault"]
    started_at: datetime
    ended_at: datetime
    git_commit: str | None
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    requests: DatasetArtifact
    events: DatasetArtifact
    ground_truth: DatasetArtifact
    recovery_verified: bool
    accepted: Literal[True] = True
