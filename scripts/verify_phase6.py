from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.artifacts import FinalRunManifest  # noqa: E402
from datasets.contracts import FinalDatasetPlan  # noqa: E402
from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.final_plan import DEFAULT_OUTPUT, validate_final_plan  # noqa: E402
from datasets.smoke_executor import FORBIDDEN_EVENT_KEYS, SECRET_MARKERS  # noqa: E402
from experiments.manifest import sha256_file  # noqa: E402
from scripts.verify_phase5 import contains_forbidden_key, read_jsonl  # noqa: E402


def in_partition(split: str, partition: str) -> bool:
    return (
        partition == "all"
        or (partition == "eligible" and split != "evaluation_only")
        or split == partition
    )


def verify_final_dataset(
    plan_path: Path = DEFAULT_OUTPUT,
    dataset_root: Path = DATASET_ROOT,
    partition: str = "eligible",
    require_complete: bool = False,
) -> tuple[list[FinalRunManifest], list[str]]:
    try:
        plan = FinalDatasetPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"invalid plan: {exc}"]
    errors = validate_final_plan(plan)
    plan_hash = sha256_file(plan_path)
    specs = {run.run_id: run for run in plan.runs if in_partition(run.split, partition)}
    manifests: list[FinalRunManifest] = []
    for path in sorted((dataset_root / "manifests").glob("*.json")):
        try:
            manifest = FinalRunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"invalid manifest {path.name}: {exc}")
            continue
        spec = specs.get(manifest.run_id)
        if spec is None:
            continue
        manifests.append(manifest)
        if manifest.plan_sha256 != plan_hash:
            errors.append(f"{manifest.run_id}: plan checksum mismatch")
        if (
            manifest.scenario_type,
            manifest.split,
            manifest.training_eligible,
            manifest.threshold_tuning_eligible,
        ) != (
            spec.scenario_type,
            spec.split,
            spec.training_eligible,
            spec.threshold_tuning_eligible,
        ):
            errors.append(f"{manifest.run_id}: manifest policy mismatch")
        loaded: dict[str, list[dict]] = {}
        truth = None
        for name, item in (
            ("requests", manifest.requests),
            ("events", manifest.events),
            ("ground_truth", manifest.ground_truth),
        ):
            artifact_path = PROJECT_DIRECTORY / item.path
            if not artifact_path.exists():
                errors.append(f"{manifest.run_id}: {name} missing")
                continue
            if sha256_file(artifact_path) != item.sha256:
                errors.append(f"{manifest.run_id}: {name} checksum mismatch")
            if name == "ground_truth":
                truth = json.loads(artifact_path.read_text(encoding="utf-8"))
            else:
                loaded[name] = read_jsonl(artifact_path)
                if len(loaded[name]) != item.record_count:
                    errors.append(f"{manifest.run_id}: {name} record count mismatch")
        if truth is not None:
            expected_fault = spec.fault_id.value if spec.fault_id else None
            expected_intensity = spec.intensity.value if spec.intensity else None
            expected = (
                spec.run_id,
                manifest.attempt_id,
                spec.split,
                spec.training_eligible,
                spec.threshold_tuning_eligible,
                expected_fault,
                expected_intensity,
            )
            actual = (
                truth.get("run_id"),
                truth.get("attempt_id"),
                truth.get("split"),
                truth.get("training_eligible"),
                truth.get("threshold_tuning_eligible"),
                truth.get("fault_id"),
                truth.get("intensity"),
            )
            if actual != expected:
                errors.append(f"{manifest.run_id}: protected ground-truth mismatch")
        requests, events = loaded.get("requests", []), loaded.get("events", [])
        if any("interval" in item for item in requests):
            errors.append(f"{manifest.run_id}: interval label leaked into requests")
        if any(event.get("run_id") != manifest.run_id for event in events):
            errors.append(f"{manifest.run_id}: mixed event run IDs")
        if any(
            contains_forbidden_key(event) or FORBIDDEN_EVENT_KEYS.intersection(event)
            for event in events
        ):
            errors.append(f"{manifest.run_id}: fault label leaked into events")
        serialized = json.dumps({"requests": requests, "events": events}).lower()
        if any(marker in serialized for marker in SECRET_MARKERS):
            errors.append(f"{manifest.run_id}: secret leaked")
        if not manifest.recovery_verified:
            errors.append(f"{manifest.run_id}: recovery not verified")
    run_ids = [item.run_id for item in manifests]
    if len(run_ids) != len(set(run_ids)):
        errors.append("duplicate run IDs found")
    if require_complete:
        missing = set(specs) - set(run_ids)
        if missing:
            errors.append(f"missing {len(missing)} of {len(specs)} planned {partition} runs")
    return manifests, list(dict.fromkeys(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 6 final dataset artifacts")
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--partition", choices=("eligible", "evaluation_only", "all"), default="eligible"
    )
    parser.add_argument("--minimum-runs", type=int, default=1)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    manifests, errors = verify_final_dataset(
        args.plan, partition=args.partition, require_complete=args.require_complete
    )
    if len(manifests) < args.minimum_runs:
        errors.append(f"accepted manifests={len(manifests)}; require {args.minimum_runs}")
    if errors:
        print("FAIL: Phase 6 final-dataset verification failed", file=sys.stderr)
        for error in errors[:40]:
            print(f"- {error}", file=sys.stderr)
        return 1
    counts = Counter(item.scenario_type for item in manifests)
    print(f"PASS: Verified {len(manifests)} Phase 6 runs")
    print(f"Scenarios: {dict(counts)}")
    print("Checksums, policy isolation, correlation metadata and recovery: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
