from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.final_plan import DEFAULT_OUTPUT  # noqa: E402
from datasets.final_quality_profile import (  # noqa: E402
    profile_final_manifests,
    validate_final_profile,
    write_final_profile,
)
from scripts.verify_phase6 import verify_final_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile Phase 6 final-dataset statistical quality"
    )
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--output",
        type=Path,
        default=DATASET_ROOT / "quality" / "profile.json",
    )
    args = parser.parse_args()
    manifests, errors = verify_final_dataset(
        args.plan,
        partition="all",
        require_complete=True,
    )
    report = None
    checksum = None
    if not errors:
        try:
            report = profile_final_manifests(PROJECT_DIRECTORY, manifests)
            errors = validate_final_profile(report)
            checksum = write_final_profile(args.output, report)
        except (OSError, ValueError) as exc:
            errors = [f"unable to profile final dataset: {exc}"]
    if errors:
        print("FAIL: Phase 6C final-dataset quality profile failed", file=sys.stderr)
        for error in errors[:40]:
            print(f"- {error}", file=sys.stderr)
        if report is not None:
            print(f"- diagnostic profile written to {args.output}", file=sys.stderr)
            print(f"- diagnostic profile SHA256: {checksum}", file=sys.stderr)
            failed_signals = [
                item["run_id"]
                for item in report["run_profiles"]
                if item["scenario_type"] != "normal" and not item["signal_detected"]
            ]
            failed_recovery = [
                item["run_id"]
                for item in report["run_profiles"]
                if item["scenario_type"] != "normal" and not item["recovery_healthy"]
            ]
            if failed_signals:
                print(f"- no measurable signal: {', '.join(failed_signals[:20])}", file=sys.stderr)
            if failed_recovery:
                print(f"- unhealthy recovery: {', '.join(failed_recovery[:20])}", file=sys.stderr)
        return 1
    assert report is not None
    print("PASS: Phase 6C final-dataset quality profile verified")
    print(f"Runs: {report['manifest_count']}")
    print(f"Requests: {report['request_count']}")
    print(f"Events: {report['event_count']}")
    print(f"Correlation-complete runs: {report['correlation_complete_runs']}")
    print(
        "Ground-truth interval assignment: "
        f"requests={report['request_interval_assignment_rate']:.2%}, "
        f"events={report['event_interval_assignment_rate']:.2%}"
    )
    print(
        f"Measurable fault signals: {report['fault_signal_runs']}/"
        f"{report['fault_run_count']}"
    )
    print(
        f"Healthy recovery intervals: {report['recovery_healthy_runs']}/"
        f"{report['fault_run_count']}"
    )
    print(f"Known fault classes: {len(report['known_fault_counts'])}")
    print(f"Sealed unknown families: {len(report['sealed_unknown_counts'])}")
    print(f"Services observed: {len(report['service_counts'])}")
    print(f"Event types observed: {len(report['event_type_counts'])}")
    print("Optional nulls: profiled separately by event type")
    print(f"Profile SHA256: {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
