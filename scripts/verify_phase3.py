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

from experiments.manifest import ExperimentManifest, sha256_file  # noqa: E402
from experiments.runner import DEFAULT_OUTPUT_ROOT, validate_correlated_run  # noqa: E402
from experiments.seeds import official_seed_pairs  # noqa: E402

FORBIDDEN_EVENT_KEYS = {"fault_active", "fault_label", "fault_origin", "probable_origin"}
SECRET_MARKERS = ("faultweave-demo", "invalid-demo-password", "bearer ey", "service-token")


def contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(FORBIDDEN_EVENT_KEYS.intersection(value)) or any(
            contains_forbidden_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(contains_forbidden_key(item) for item in value)
    return False


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


def verify_manifest(path: Path, project_directory: Path) -> tuple[ExperimentManifest, list[str]]:
    errors: list[str] = []
    try:
        manifest = ExperimentManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid manifest {path.name}: {exc}") from exc
    requests_path = project_directory / manifest.artifacts.requests_path
    events_path = project_directory / manifest.artifacts.events_path
    if not requests_path.exists() or not events_path.exists():
        return manifest, [f"{manifest.run_id}: artifact file missing"]
    if sha256_file(requests_path) != manifest.artifacts.requests_sha256:
        errors.append(f"{manifest.run_id}: request checksum mismatch")
    if sha256_file(events_path) != manifest.artifacts.events_sha256:
        errors.append(f"{manifest.run_id}: event checksum mismatch")
    requests = read_jsonl(requests_path)
    events = read_jsonl(events_path)
    if len(requests) != manifest.summary.scheduled_requests:
        errors.append(f"{manifest.run_id}: request count mismatch")
    if len(events) != manifest.summary.correlated_event_count:
        errors.append(f"{manifest.run_id}: event count mismatch")
    if any(event.get("schema_version") != "1.1" for event in events):
        errors.append(f"{manifest.run_id}: wrong event schema")
    if any(event.get("run_id") != manifest.run_id for event in events):
        errors.append(f"{manifest.run_id}: mixed run IDs")
    if any(contains_forbidden_key(event) for event in events):
        errors.append(f"{manifest.run_id}: fault or origin label leaked into events")
    errors.extend(validate_correlated_run(requests, events))
    serialized = json.dumps({"requests": requests, "events": events}).lower()
    if any(marker in serialized for marker in SECRET_MARKERS):
        errors.append(f"{manifest.run_id}: secret marker found")
    if manifest.fault_injection_enabled:
        errors.append(f"{manifest.run_id}: fault injection was enabled")
    if manifest.summary.expected_outcome_rate < 0.99:
        errors.append(f"{manifest.run_id}: expected outcome rate below 0.99")
    if manifest.summary.transport_errors or manifest.summary.server_errors:
        errors.append(f"{manifest.run_id}: transport or server errors present")
    return manifest, errors


def verify_phase3(
    minimum_runs: int,
    require_official: bool,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> tuple[list[ExperimentManifest], list[str]]:
    project_directory = output_root.parents[1]
    paths = sorted((output_root / "manifests").glob("*.json"))
    errors: list[str] = []
    manifests: list[ExperimentManifest] = []
    for path in paths:
        try:
            manifest, manifest_errors = verify_manifest(path, project_directory)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        manifests.append(manifest)
        errors.extend(manifest_errors)
    if len(manifests) < minimum_runs:
        errors.append(f"found {len(manifests)} manifests; require at least {minimum_runs}")
    run_ids = [manifest.run_id for manifest in manifests]
    if len(run_ids) != len(set(run_ids)):
        errors.append("duplicate run IDs found")
    if require_official:
        official_manifests = [manifest for manifest in manifests if manifest.purpose == "official"]
        observed = {(manifest.profile, manifest.seed) for manifest in official_manifests}
        missing = set(official_seed_pairs()) - observed
        if missing:
            errors.append(f"missing {len(missing)} official profile/seed pairs")
        if len(official_manifests) < minimum_runs:
            errors.append(
                f"found {len(official_manifests)} official manifests; require {minimum_runs}"
            )
    return manifests, list(dict.fromkeys(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify FaultWeave Phase 3 experiment artifacts")
    parser.add_argument("--minimum-runs", type=int, default=1)
    parser.add_argument("--require-official", action="store_true")
    args = parser.parse_args()
    if args.minimum_runs < 1:
        parser.error("--minimum-runs must be positive")
    return args


def main() -> int:
    args = parse_args()
    manifests, errors = verify_phase3(args.minimum_runs, args.require_official)
    if errors:
        print("FAIL: Phase 3 experiment verification failed", file=sys.stderr)
        for error in errors[:30]:
            print(f"- {error}", file=sys.stderr)
        return 1
    profile_counts = Counter(manifest.profile for manifest in manifests)
    print(f"PASS: Verified {len(manifests)} normal experiment manifests")
    print(
        "Profiles: "
        + ", ".join(f"{profile}={count}" for profile, count in sorted(profile_counts.items()))
    )
    print("Checksums: valid")
    print("Correlation: valid")
    print("Fault injection: disabled")
    print("Secrets and label leakage: not present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
