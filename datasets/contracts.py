from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.faults.catalog import FaultFamily, FaultIntensity
from experiments.sealed_unknowns.catalog import SealedUnknownFamily


class IntervalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["normal", "baseline", "fault", "recovery"]
    offset_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)

    @property
    def end_seconds(self) -> float:
        return self.offset_seconds + self.duration_seconds


class RunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(pattern=r"^[a-z0-9-]{1,75}$")
    scenario_type: Literal["normal", "known_fault"]
    replicate: int = Field(ge=1, le=5)
    seed: int = Field(gt=0)
    profile: str
    fault_id: FaultFamily | None = None
    target: str | None = None
    intensity: FaultIntensity | None = None
    intervals: tuple[IntervalSpec, ...]
    training_eligible: Literal[True] = True

    @model_validator(mode="after")
    def validate_scenario_fields(self) -> RunSpec:
        fault_fields = (self.fault_id, self.target, self.intensity)
        if self.scenario_type == "normal":
            if any(value is not None for value in fault_fields):
                raise ValueError("normal run cannot contain fault metadata")
            if tuple(item.name for item in self.intervals) != ("normal",):
                raise ValueError("normal run must contain exactly one normal interval")
        else:
            if any(value is None for value in fault_fields):
                raise ValueError("known-fault run requires fault, target and intensity")
            if tuple(item.name for item in self.intervals) != (
                "baseline",
                "fault",
                "recovery",
            ):
                raise ValueError("known-fault intervals must be baseline, fault, recovery")
        expected_offset = 0.0
        for interval in self.intervals:
            if interval.offset_seconds != expected_offset:
                raise ValueError("run intervals must be contiguous and begin at zero")
            expected_offset = interval.end_seconds
        return self


class DatasetPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str = Field(pattern=r"^smoke-dataset-v[0-9]+$")
    purpose: Literal["smoke"] = "smoke"
    created_at: datetime
    plan_seed: int
    normal_run_count: Literal[10] = 10
    known_runs_per_class: Literal[5] = 5
    expected_run_count: Literal[55] = 55
    sealed_unknowns_allowed: Literal[False] = False
    runs: tuple[RunSpec, ...]

    @model_validator(mode="after")
    def validate_matrix(self) -> DatasetPlan:
        if len(self.runs) != self.expected_run_count:
            raise ValueError("smoke plan must contain exactly 55 runs")
        run_ids = [run.run_id for run in self.runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("smoke plan contains duplicate run IDs")
        return self


class FinalRunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(pattern=r"^[a-z0-9-]{1,75}$")
    scenario_type: Literal["normal", "known_fault", "sealed_unknown"]
    split: Literal["train", "validation", "test", "evaluation_only"]
    replicate: int = Field(ge=1, le=20)
    seed: int = Field(gt=0)
    profile: str
    fault_id: FaultFamily | SealedUnknownFamily | None = None
    target: str | None = None
    intensity: FaultIntensity | None = None
    intervals: tuple[IntervalSpec, ...]
    training_eligible: bool
    threshold_tuning_eligible: bool

    @model_validator(mode="after")
    def validate_isolation(self) -> FinalRunSpec:
        fault_fields = (self.fault_id, self.target, self.intensity)
        if self.scenario_type == "normal":
            if any(value is not None for value in fault_fields):
                raise ValueError("normal run cannot contain fault metadata")
            if tuple(item.name for item in self.intervals) != ("normal",):
                raise ValueError("normal run must contain one normal interval")
        else:
            if any(value is None for value in fault_fields):
                raise ValueError("fault run requires fault, target and intensity")
            if tuple(item.name for item in self.intervals) != ("baseline", "fault", "recovery"):
                raise ValueError("fault run intervals must be baseline, fault, recovery")
        if self.scenario_type == "sealed_unknown":
            if self.split != "evaluation_only":
                raise ValueError("sealed unknown run must be evaluation-only")
            if self.training_eligible or self.threshold_tuning_eligible:
                raise ValueError("sealed unknown run cannot train or tune thresholds")
        elif self.split == "evaluation_only":
            raise ValueError("normal and known runs require train/validation/test split")
        expected_offset = 0.0
        for interval in self.intervals:
            if interval.offset_seconds != expected_offset:
                raise ValueError("run intervals must be contiguous and begin at zero")
            expected_offset = interval.end_seconds
        return self


class FinalDatasetPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    dataset_id: Literal["faultweave-final-dataset-v1"] = "faultweave-final-dataset-v1"
    purpose: Literal["final_research"] = "final_research"
    created_at: datetime
    plan_seed: int
    normal_run_count: Literal[60] = 60
    known_runs_per_class: Literal[20] = 20
    sealed_unknown_runs_per_family: Literal[20] = 20
    expected_run_count: Literal[280] = 280
    runs: tuple[FinalRunSpec, ...]

    @model_validator(mode="after")
    def validate_matrix(self) -> FinalDatasetPlan:
        if len(self.runs) != self.expected_run_count:
            raise ValueError("final plan must contain exactly 280 runs")
        run_ids = [run.run_id for run in self.runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("final plan contains duplicate run IDs")
        return self
