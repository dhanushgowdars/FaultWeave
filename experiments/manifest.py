from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MANIFEST_SCHEMA_VERSION = "1.0"


class StatusCount(BaseModel):
    status_code: int
    count: int = Field(ge=0)


class LatencySummary(BaseModel):
    minimum_ms: float = Field(ge=0)
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    p99_ms: float = Field(ge=0)
    maximum_ms: float = Field(ge=0)


class RunSummary(BaseModel):
    scheduled_requests: int = Field(ge=1)
    completed_responses: int = Field(ge=0)
    expected_outcomes: int = Field(ge=0)
    completed_transactions: int = Field(ge=0)
    unexpected_outcomes: int = Field(ge=0)
    transport_errors: int = Field(ge=0)
    server_errors: int = Field(ge=0)
    response_rate: float = Field(ge=0, le=1)
    success_rate: float = Field(ge=0, le=1)
    expected_outcome_rate: float = Field(ge=0, le=1)
    completed_transaction_rate: float = Field(ge=0, le=1)
    achieved_rps: float = Field(ge=0)
    status_counts: list[StatusCount]
    scenario_counts: dict[str, int]
    latency: LatencySummary
    correlated_event_count: int = Field(ge=0)


class RunArtifacts(BaseModel):
    requests_path: str
    events_path: str
    requests_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    events_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExperimentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_schema_version: Literal["1.0"] = MANIFEST_SCHEMA_VERSION
    event_schema_version: Literal["1.1"] = "1.1"
    run_id: str
    run_type: Literal["normal"] = "normal"
    purpose: Literal["ad_hoc", "calibration", "smoke", "official"] = "ad_hoc"
    profile: str
    seed: int
    configured_duration_seconds: float = Field(gt=0)
    actual_duration_seconds: float = Field(gt=0)
    target_rps: float = Field(gt=0)
    rate_segments: list[dict[str, float]]
    started_at: datetime
    ended_at: datetime
    gateway_url: str
    git_commit: str | None
    fault_injection_enabled: Literal[False] = False
    summary: RunSummary
    artifacts: RunArtifacts


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: BaseModel | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
