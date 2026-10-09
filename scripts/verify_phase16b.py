"""Run and verify every controlled transfer fault case against live Docker services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from experiments.faults.catalog import FaultFamily
from experiments.manifest import sha256_file, write_json
from experiments.transfer_fault_cases import TRANSFER_FAULT_CASES
from scripts.phase16_transfer_fault_case import (
    CONTROL_DIRECTORY,
    ROOT,
    resume_case,
    start_case,
)


def validate_case(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "RECOVERY_VERIFIED":
        raise RuntimeError(f"case did not recover: {manifest_path}")
    if manifest.get("recovery_verified") is not True:
        raise RuntimeError(f"case has no recovery proof: {manifest_path}")
    truth_path = ROOT / manifest["ground_truth"]["path"]
    observable_path = ROOT / manifest["observable_case"]["path"]
    if sha256_file(truth_path) != manifest["ground_truth"]["sha256"]:
        raise RuntimeError(f"ground-truth checksum failed: {manifest_path}")
    if sha256_file(observable_path) != manifest["observable_case"]["sha256"]:
        raise RuntimeError(f"observable checksum failed: {manifest_path}")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    observable = json.loads(observable_path.read_text(encoding="utf-8"))
    if truth.get("protected") is not True or truth.get("excluded_from_inference") is not True:
        raise RuntimeError(f"ground truth is not isolated: {manifest_path}")
    if observable.get("status") != "RECOVERY_VERIFIED":
        raise RuntimeError(f"observable case is incomplete: {manifest_path}")
    initial = observable["case_evidence"]
    recovered = observable["recovered_case_evidence"]
    if initial["state"] != "REQUIRES_REVIEW" or recovered["state"] != "COMPLETED":
        raise RuntimeError(f"case did not preserve pause and completion: {manifest_path}")
    history = recovered.get("case_history", {})
    if not all(history.get(name) for name in ("failures", "reviews", "recoveries")):
        raise RuntimeError(f"case history is incomplete: {manifest_path}")
    journal = recovered["payment"]["evidence"]["ledger_posting"]["evidence"]["entries"]
    if len(journal) != 2 or {entry["direction"] for entry in journal} != {
        "DEBIT",
        "CREDIT",
    }:
        raise RuntimeError(f"case journal is not balanced: {manifest_path}")
    serialized = observable_path.read_text(encoding="utf-8")
    if any(fault.value in serialized for fault in FaultFamily):
        raise RuntimeError(f"fault label leaked into observable evidence: {manifest_path}")
    return {
        "case_id": manifest["case_id"],
        "run_id": manifest["run_id"],
        "transfer_id": manifest["transfer_id"],
        "manifest": str(manifest_path.relative_to(ROOT)),
    }


def main() -> int:
    completed = []
    try:
        for case_id in TRANSFER_FAULT_CASES:
            print(f"RUN: {case_id}", flush=True)
            manifest_path = start_case(case_id)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            resume_case(
                manifest["run_id"],
                "Engineer reviewed stored evidence and restored the affected dependency",
            )
            completed.append(validate_case(manifest_path))
            print(f"PASS: {case_id}", flush=True)
        if (CONTROL_DIRECTORY / "active_fault.json").exists():
            raise RuntimeError("active fault lease remained after acceptance")
        summary = (
            ROOT
            / "data"
            / "experiments"
            / "transfer-fault-cases"
            / f"acceptance-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        write_json(
            summary,
            {
                "schema_version": "1.0",
                "created_at": datetime.now(UTC).isoformat(),
                "case_count": len(completed),
                "cases": completed,
                "ground_truth_isolated": True,
                "recovery_verified": True,
            },
        )
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"PASS: {len(completed)} controlled transfer fault cases verified")
    print("Each case preserved failure, engineer review, recovery, and balanced journal evidence")
    print(f"Acceptance summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
