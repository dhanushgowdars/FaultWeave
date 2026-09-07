from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.artifacts import SmokeRunManifest  # noqa: E402
from datasets.contracts import DatasetPlan  # noqa: E402
from datasets.smoke_executor import (  # noqa: E402
    DATASET_ROOT,
    FORBIDDEN_EVENT_KEYS,
    SECRET_MARKERS,
)
from datasets.smoke_plan import DEFAULT_OUTPUT, validate_smoke_plan  # noqa: E402
from experiments.faults.catalog import FaultFamily  # noqa: E402
from experiments.manifest import sha256_file  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"non-object JSONL at {path}:{line_number}")
            records.append(value)
    return records


def contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(FORBIDDEN_EVENT_KEYS.intersection(value)) or any(
            contains_forbidden_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(contains_forbidden_key(item) for item in value)
    return False


def verify_run(
    manifest_path: Path, plan: DatasetPlan, plan_hash: str
) -> tuple[SmokeRunManifest | None, list[str]]:
    errors: list[str] = []
    try:
        manifest = SmokeRunManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        return None, [f"invalid manifest {manifest_path.name}: {exc}"]
    specs = {run.run_id: run for run in plan.runs}
    spec = specs.get(manifest.run_id)
    if spec is None:
        return manifest, [f"{manifest.run_id}: run is not in frozen plan"]
    if manifest.plan_sha256 != plan_hash:
        errors.append(f"{manifest.run_id}: plan checksum mismatch")
    if manifest.scenario_type != spec.scenario_type:
        errors.append(f"{manifest.run_id}: scenario type mismatch")

    loaded: dict[str, list[dict[str, Any]]] = {}
    ground_truth: dict[str, Any] | None = None
    for name, item in (
        ("requests", manifest.requests),
        ("events", manifest.events),
        ("ground_truth", manifest.ground_truth),
    ):
        path = PROJECT_DIRECTORY / item.path
        if not path.exists():
            errors.append(f"{manifest.run_id}: {name} artifact missing")
            continue
        if sha256_file(path) != item.sha256:
            errors.append(f"{manifest.run_id}: {name} checksum mismatch")
        if name != "ground_truth":
            try:
                loaded[name] = read_jsonl(path)
            except ValueError as exc:
                errors.append(str(exc))
            if name in loaded and len(loaded[name]) != item.record_count:
                errors.append(f"{manifest.run_id}: {name} record count mismatch")
        else:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                ground_truth = parsed if isinstance(parsed, dict) else None
            except (OSError, json.JSONDecodeError):
                ground_truth = None
            if ground_truth is None:
                errors.append(f"{manifest.run_id}: invalid ground-truth artifact")

    if ground_truth is not None:
        expected_fault = spec.fault_id.value if spec.fault_id else None
        expected_intensity = spec.intensity.value if spec.intensity else None
        if ground_truth.get("run_id") != manifest.run_id:
            errors.append(f"{manifest.run_id}: ground-truth run ID mismatch")
        if ground_truth.get("attempt_id") != manifest.attempt_id:
            errors.append(f"{manifest.run_id}: ground-truth attempt ID mismatch")
        if ground_truth.get("scenario_type") != spec.scenario_type:
            errors.append(f"{manifest.run_id}: ground-truth scenario mismatch")
        if ground_truth.get("fault_id") != expected_fault:
            errors.append(f"{manifest.run_id}: ground-truth fault mismatch")
        if ground_truth.get("target") != spec.target:
            errors.append(f"{manifest.run_id}: ground-truth target mismatch")
        if ground_truth.get("intensity") != expected_intensity:
            errors.append(f"{manifest.run_id}: ground-truth intensity mismatch")
        if ground_truth.get("training_eligible") is not True:
            errors.append(f"{manifest.run_id}: run is not marked training eligible")
        interval_names = [item.get("name") for item in ground_truth.get("intervals", [])]
        if interval_names != [item.name for item in spec.intervals]:
            errors.append(f"{manifest.run_id}: ground-truth intervals mismatch")

    requests = loaded.get("requests", [])
    events = loaded.get("events", [])
    if any("interval" in item for item in requests):
        errors.append(f"{manifest.run_id}: interval label leaked into request observations")
    if any(event.get("run_id") != manifest.run_id for event in events):
        errors.append(f"{manifest.run_id}: mixed event run IDs")
    if any(contains_forbidden_key(event) for event in events):
        errors.append(f"{manifest.run_id}: fault label leaked into events")
    serialized = json.dumps({"requests": requests, "events": events}).lower()
    if any(marker in serialized for marker in SECRET_MARKERS):
        errors.append(f"{manifest.run_id}: secret leaked into observable artifacts")
    if not manifest.recovery_verified:
        errors.append(f"{manifest.run_id}: recovery not verified")
    return manifest, errors


def verify_dataset(
    plan_path: Path = DEFAULT_OUTPUT,
    dataset_root: Path = DATASET_ROOT,
    require_complete: bool = False,
) -> tuple[list[SmokeRunManifest], list[str]]:
    try:
        plan = DatasetPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"invalid plan: {exc}"]
    errors = validate_smoke_plan(plan)
    plan_hash = sha256_file(plan_path)
    manifests: list[SmokeRunManifest] = []
    for path in sorted((dataset_root / "manifests").glob("*.json")):
        manifest, run_errors = verify_run(path, plan, plan_hash)
        if manifest is not None:
            manifests.append(manifest)
        errors.extend(run_errors)
    run_ids = [manifest.run_id for manifest in manifests]
    if len(run_ids) != len(set(run_ids)):
        errors.append("duplicate run IDs found")
    if require_complete:
        missing = {run.run_id for run in plan.runs} - set(run_ids)
        if missing:
            errors.append(f"missing {len(missing)} of 55 planned runs")
        counts = Counter(
            run.fault_id for run in plan.runs if run.run_id in run_ids and run.fault_id
        )
        for family in FaultFamily:
            if counts[family] != 5:
                errors.append(f"{family.value}: accepted run count is {counts[family]}, expected 5")
    return manifests, list(dict.fromkeys(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 5 smoke artifacts")
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--minimum-runs", type=int, default=1)
    args = parser.parse_args()
    manifests, errors = verify_dataset(args.plan, require_complete=args.require_complete)
    if len(manifests) < args.minimum_runs:
        errors.append(f"accepted manifests={len(manifests)}; require {args.minimum_runs}")
    if errors:
        print("FAIL: Phase 5 smoke-dataset verification failed", file=sys.stderr)
        for error in errors[:40]:
            print(f"- {error}", file=sys.stderr)
        return 1
    counts = Counter(manifest.scenario_type for manifest in manifests)
    print(f"PASS: Verified {len(manifests)} smoke-dataset runs")
    print(f"Normal: {counts['normal']}")
    print(f"Known fault: {counts['known_fault']}")
    print("Checksums and record counts: valid")
    print("Observable label and secret leakage: none")
    print("Recovery: verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
