from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.final_features import (  # noqa: E402
    build_final_feature_dataset,
    validate_feature_report,
)
from datasets.final_plan import DEFAULT_OUTPUT  # noqa: E402
from scripts.verify_phase6 import verify_final_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build leakage-safe Phase 7 feature windows")
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DATASET_ROOT / "features")
    args = parser.parse_args()
    manifests, errors = verify_final_dataset(args.plan, partition="all", require_complete=True)
    if not errors:
        try:
            report = build_final_feature_dataset(PROJECT_DIRECTORY, manifests, args.output)
            errors = validate_feature_report(report)
        except (OSError, ValueError) as exc:
            errors = [f"unable to build feature dataset: {exc}"]
    if errors:
        print("FAIL: Phase 7 feature build failed", file=sys.stderr)
        for error in errors[:40]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PASS: Phase 7 feature windows created")
    print("Window sizes: 10s, 30s, 60s")
    for seconds, item in report["outputs"].items():
        print(f"{seconds}s: {item['record_count']} windows; SHA256={item['sha256']}")
    print(f"Report SHA256: {report['report_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
