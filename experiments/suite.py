from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from experiments.manifest import ExperimentManifest, sha256_file, write_json
from experiments.runner import DEFAULT_OUTPUT_ROOT, run_experiment
from experiments.seeds import OFFICIAL_NORMAL_SEEDS

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]


def calibrated_high_rps(output_root: Path) -> float:
    path = output_root / "calibration" / "healthy_envelope.json"
    if not path.exists():
        raise RuntimeError("run experiments.calibrate before the official suite")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream).get("selected_high_healthy_rps")
    if value is None:
        raise RuntimeError("healthy traffic calibration has no accepted rate")
    return float(value)


def existing_pairs(
    output_root: Path,
    purpose: str,
) -> dict[tuple[str, int], ExperimentManifest]:
    manifests: dict[tuple[str, int], ExperimentManifest] = {}
    for path in (output_root / "manifests").glob("*.json"):
        try:
            manifest = ExperimentManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        requests_path = PROJECT_DIRECTORY / manifest.artifacts.requests_path
        events_path = PROJECT_DIRECTORY / manifest.artifacts.events_path
        artifacts_valid = (
            requests_path.exists()
            and events_path.exists()
            and sha256_file(requests_path) == manifest.artifacts.requests_sha256
            and sha256_file(events_path) == manifest.artifacts.events_sha256
        )
        run_valid = (
            manifest.summary.expected_outcome_rate >= 0.99
            and manifest.summary.transport_errors == 0
            and manifest.summary.server_errors == 0
        )
        if manifest.purpose == purpose and artifacts_valid and run_valid:
            manifests[(manifest.profile, manifest.seed)] = manifest
    return manifests


def run_suite(
    runs_per_profile: int,
    duration_seconds: float | None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict:
    high_rps = calibrated_high_rps(output_root)
    purpose = "official" if runs_per_profile == 5 and duration_seconds is None else "smoke"
    existing = existing_pairs(output_root, purpose)
    completed: list[ExperimentManifest] = []
    for profile, seeds in OFFICIAL_NORMAL_SEEDS.items():
        for seed in seeds[:runs_per_profile]:
            pair = (profile, seed)
            if pair in existing:
                manifest = existing[pair]
                print(f"RESUME: {profile} seed={seed} run={manifest.run_id}")
            else:
                print(f"RUN: {profile} seed={seed}")
                manifest = run_experiment(
                    profile,
                    seed,
                    duration_seconds=duration_seconds,
                    target_rps=high_rps if profile == "high_healthy" else None,
                    output_root=output_root,
                    purpose=purpose,
                )
            completed.append(manifest)

    suite_id = datetime.now(UTC).strftime("normal-suite-%Y%m%dT%H%M%SZ")
    result = {
        "schema_version": "1.0",
        "suite_id": suite_id,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_type": "normal",
        "purpose": purpose,
        "runs_per_profile": runs_per_profile,
        "run_count": len(completed),
        "profiles": sorted(OFFICIAL_NORMAL_SEEDS),
        "calibrated_high_healthy_rps": high_rps,
        "run_ids": [manifest.run_id for manifest in completed],
    }
    write_json(output_root / "suites" / f"{suite_id}.json", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the official seeded normal suite")
    parser.add_argument("--runs-per-profile", type=int, default=5)
    parser.add_argument("--duration", type=float)
    args = parser.parse_args()
    if not 1 <= args.runs_per_profile <= 5:
        parser.error("--runs-per-profile must be from 1 through 5")
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")
    return args


def main() -> int:
    args = parse_args()
    try:
        result = run_suite(args.runs_per_profile, args.duration)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"PASS: {result['run_count']} seeded normal experiments completed")
    print(f"Suite ID: {result['suite_id']}")
    print(f"High-but-healthy rate: {result['calibrated_high_healthy_rps']:.2f} req/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
