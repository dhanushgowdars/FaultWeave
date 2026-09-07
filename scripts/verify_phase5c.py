from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.quality_profile import (  # noqa: E402
    profile_manifests,
    validate_profile,
    write_profile,
)
from datasets.smoke_executor import DATASET_ROOT  # noqa: E402
from datasets.smoke_plan import DEFAULT_OUTPUT  # noqa: E402
from scripts.verify_phase5 import verify_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile Phase 5 smoke-dataset quality")
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DATASET_ROOT / "quality" / "profile.json")
    args = parser.parse_args()
    manifests, errors = verify_dataset(args.plan, require_complete=True)
    checksum = None
    if not errors:
        report = profile_manifests(PROJECT_DIRECTORY, manifests)
        errors = validate_profile(report)
        if not errors:
            checksum = write_profile(args.output, report)
    if errors:
        print("FAIL: Phase 5C dataset-quality profile failed", file=sys.stderr)
        for error in errors[:40]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PASS: Phase 5C dataset-quality profile verified")
    print(f"Runs: {report['manifest_count']}")
    print(f"Requests: {report['request_count']}")
    print(f"Events: {report['event_count']}")
    print(f"Correlation-complete runs: {report['correlation_complete_runs']}")
    print(f"Known fault classes: {len(report['known_fault_counts'])}")
    print("Sealed unknown contamination: none")
    print(f"Profile SHA256: {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
