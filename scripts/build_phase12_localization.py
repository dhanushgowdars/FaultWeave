from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.incident_localization import (  # noqa: E402
    build_localization_report,
    validate_localization_report,
)


def main() -> int:
    output_root = DATASET_ROOT / "localization"
    try:
        report = build_localization_report(
            PROJECT_DIRECTORY,
            DATASET_ROOT,
            PROJECT_DIRECTORY / "config" / "service_dependencies.json",
            output_root,
        )
        errors = validate_localization_report(report)
    except (OSError, ValueError) as exc:
        print(f"FAIL: Phase 12 localization build failed\n- {exc}", file=sys.stderr)
        return 1
    if errors:
        print("FAIL: Phase 12 localization verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    metrics = report["metrics"]
    print("PASS: Phase 12 incident-localization report built")
    print(f"Abnormal runs: {metrics['overall']['runs']}")
    print(f"Overall Top-1: {metrics['overall']['top_1_accuracy']:.6f}")
    print(f"Overall Top-3: {metrics['overall']['top_3_accuracy']:.6f}")
    print(f"Known Top-1: {metrics['known_fault']['top_1_accuracy']:.6f}")
    print(f"Known Top-3: {metrics['known_fault']['top_3_accuracy']:.6f}")
    print(f"Unknown Top-1: {metrics['sealed_unknown']['top_1_accuracy']:.6f}")
    print(f"Unknown Top-3: {metrics['sealed_unknown']['top_3_accuracy']:.6f}")
    print(f"Report SHA256: {report['report_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
