"""FaultWeave Phase 19C.2E — deterministic, blocked, offline v2 dataset draft.

This is a planning tool, NOT an experiment executor. No Docker, network,
model loading, fault activation, or training. The draft is NOT approved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.faults.catalog import FaultFamily, FaultIntensity, get_known_fault  # noqa: E402

DRAFT_OUTPUT = PROJECT_ROOT / "data" / "datasets" / "v2" / "plan" / "phase19c2e-draft-matrix.json"
GROUPS = ("low", "nominal", "elevated")
SPLITS = (("train", 12), ("validation", 4), ("test", 4))
HEALTHY_PER_GROUP = 20
FAULT_PER_CLASS = 20
VERSION = "19c2e-draft.1"


def split_for_replicate(replicate: int) -> str:
    if not 1 <= replicate <= 20:
        raise ValueError("replicate must be 1..20")
    return "train" if replicate <= 12 else "validation" if replicate <= 16 else "test"


def rate_group(replicate: int) -> str:
    return GROUPS[(replicate - 1) % len(GROUPS)]


def execution_gate(fault_id: str | None) -> str:
    if fault_id == "HIGH_LOAD":
        return "OVERLOAD_ENVELOPE_AND_ISOLATION_CALIBRATION_REQUIRED"
    if fault_id == "AUTHENTICATION_FAILURE_BURST":
        return "AUTHENTICATION_BURST_CALIBRATION_REQUIRED"
    if fault_id == "CONNECTION_POOL_EXHAUSTION":
        return "DATABASE_POOL_SAFETY_CALIBRATION_REQUIRED"
    if fault_id == "DATABASE_UNAVAILABLE":
        return "EXPLICIT_INFRASTRUCTURE_OUTAGE_APPROVAL_REQUIRED"
    if fault_id == "DATABASE_LOCK_CONTENTION":
        return "DATABASE_LOCK_RECOVERY_CALIBRATION_REQUIRED"
    if fault_id is not None:
        return "FAULT_EXPOSURE_AND_RATE_CALIBRATION_REQUIRED"
    return "HEALTHY_RATE_AND_RECOVERY_CALIBRATION_REQUIRED"


def make_draft() -> dict:
    runs: list[dict] = []
    for group in GROUPS:
        for rep in range(1, HEALTHY_PER_GROUP + 1):
            runs.append({
                "run_id": f"v2-normal-{group}-{rep:02d}",
                "scenario_type": "normal", "split": split_for_replicate(rep),
                "rate_regime": group, "target_rps": None,
                "replicate": rep, "fault_id": None, "target": None, "intensity": None,
                "seed": 202619000 + GROUPS.index(group) * 100 + rep,
                "window_seconds": 60, "capture_seconds": 75,
                "training_eligible": False, "threshold_tuning_eligible": False,
                "execution_allowed": False, "gate": execution_gate(None),
            })
    for class_index, fault in enumerate(FaultFamily):
        definition = get_known_fault(fault)
        slug = fault.value.lower().replace("_", "-")
        for rep in range(1, FAULT_PER_CLASS + 1):
            intensity = (
                FaultIntensity.HIGH if fault == FaultFamily.CONNECTION_POOL_EXHAUSTION
                else tuple(FaultIntensity)[(((rep - 1) // 3) + 2 * ((rep - 1) % 3)) % len(FaultIntensity)]
            )
            runs.append({
                "run_id": f"v2-known-{slug}-{rep:02d}",
                "scenario_type": "known_fault", "split": split_for_replicate(rep),
                "rate_regime": rate_group(rep), "target_rps": None,
                "replicate": rep, "fault_id": fault.value,
                "target": definition.allowed_targets[(((rep - 1) // 3) + ((rep - 1) % 3)) % len(definition.allowed_targets)],
                "intensity": intensity.value,
                "seed": 202619100 + class_index * 100 + rep,
                "window_seconds": 60, "capture_seconds": 75,
                "training_eligible": False, "threshold_tuning_eligible": False,
                "execution_allowed": False, "gate": execution_gate(fault.value),
            })
    plan = {
        "schema_version": VERSION,
        "status": "DRAFT_BLOCKED_NOT_APPROVED",
        "purpose": "pre-register candidate run splits and safety prerequisites before collection",
        "model_version": "v2_planned; no artifacts created",
        "feature_schema": "UNAPPROVED; parity and load-robustness analysis required",
        "data_root": "data/datasets/v2; separate from original final dataset",
        "fresh_unknown_holdout": "NOT_DEFINED; excluded from draft and must be freshly designed/sealed",
        "calibration": {
            "existing_verified_healthy_reference_rps": 2.0,
            "healthy_reference_applies_to": "one 2-RPS lab pilot only",
            "low_rps": None, "nominal_rps": None, "elevated_rps": None,
            "per_family_fault_exposure": "not approved",
        },
        "acceptance_gates": [
            "document safe hardware-specific traffic envelopes per fault family and target",
            "confirm every planned target is present in the research-workload dependency graph",
            "require run-scoped fault activation, bounded TTL, cleanup, and verified post-clear recovery",
            "use exactly 60 seconds wholly inside confirmed fault activation for known-fault windows",
            "require full observable watermark, correlated request IDs/traces, and independently verified 65 features",
            "segregate protected labels and prevent train/validation/test/holdout run-ID overlap",
            "collect healthy controls at comparable rates before supervised fault runs",
            "do not use original sealed unknowns for tuning or claim they are a fresh holdout",
            "separately define and seal a genuinely new unknown family before one-time evaluation",
            "never execute bulk runs from this draft or treat diagnostic pilots as eligible training data",
        ],
        "runs": runs,
    }
    validate_draft(plan)
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    plan["draft_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return plan


def validate_draft(plan: dict) -> None:
    runs = plan["runs"]
    if len(runs) != 240 or len({r["run_id"] for r in runs}) != 240:
        raise ValueError("expected 240 unique runs")
    scenarios = Counter(r["scenario_type"] for r in runs)
    if scenarios != {"normal": 60, "known_fault": 180}:
        raise ValueError("scenario coverage mismatch")
    class_counts = Counter(r["fault_id"] for r in runs if r["fault_id"])
    if set(class_counts) != {f.value for f in FaultFamily} or any(n != 20 for n in class_counts.values()):
        raise ValueError("missing or imbalanced known fault class")
    splits = Counter(r["split"] for r in runs)
    if splits != {"train": 144, "validation": 48, "test": 48}:
        raise ValueError("run split counts not balanced")
    for row in runs:
        if (row["execution_allowed"] or row["training_eligible"] or row["threshold_tuning_eligible"]
            or row["target_rps"] is not None or row["window_seconds"] != 60):
            raise ValueError("draft contains unauthorized executable/eligible run")
        if row["fault_id"] is not None:
            definition = get_known_fault(row["fault_id"])
            if row["target"] not in definition.allowed_targets or FaultIntensity(row["intensity"]) not in definition.allowed_intensities:
                raise ValueError("invalid fault target or intensity")
    for class_id in [None, *(fault.value for fault in FaultFamily)]:
        sample = [r for r in runs if r["fault_id"] == class_id]
        expected = {"train": 36, "validation": 12, "test": 12} if class_id is None else {"train": 12, "validation": 4, "test": 4}
        if Counter(r["split"] for r in sample) != expected:
            raise ValueError("class-level partition balance violated")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print safe draft summary; write nothing")
    mode.add_argument("--write", action="store_true", help="create the new, blocked draft once")
    args = parser.parse_args()
    draft = make_draft()
    rows = draft["runs"]
    print("PHASE 19C.2E: OFFLINE DRAFT, NOT APPROVED OR EXECUTABLE")
    print("RUNS:", len(rows), "normal=60 known_fault=180; 9 classes x 20")
    print("SPLITS:", dict(Counter(row["split"] for row in rows)))
    print("SAFE RATE ENVELOPES: PENDING; all 240 runs blocked")
    print("FRESH UNKNOWN HOLDOUT: NOT INCLUDED; must be separately designed and sealed")
    print("DRAFT SHA256:", draft["draft_sha256"])
    if args.dry_run:
        print("DRY RUN: No files or experiment state changed.")
        return 0
    if DRAFT_OUTPUT.exists():
        print("STOP: draft output already exists; no overwrite:", DRAFT_OUTPUT)
        return 2
    DRAFT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        with DRAFT_OUTPUT.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(draft, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        print("STOP: another process created the draft; not overwritten")
        return 2
    print("CREATED BLOCKED DRAFT:", DRAFT_OUTPUT)
    print("NO Docker, fault injection, model, database, v1 files or Git operations executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
