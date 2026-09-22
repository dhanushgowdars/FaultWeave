from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.rule_baseline import (  # noqa: E402
    build_rule_baseline,
    validate_rule_baseline_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Phase 10 rule baseline")
    parser.add_argument(
        "--features",
        type=Path,
        default=DATASET_ROOT / "features" / "windows-30s.jsonl",
    )
    parser.add_argument("--report", type=Path, default=DATASET_ROOT / "features" / "report.json")
    parser.add_argument("--output", type=Path, default=DATASET_ROOT / "models" / "rule-baseline")
    args = parser.parse_args()
    try:
        result = build_rule_baseline(args.features, args.output, args.report)
        errors = validate_rule_baseline_artifacts(args.output)
    except (OSError, ValueError) as exc:
        result, errors = None, [str(exc)]
    if errors:
        print("FAIL: Phase 10 rule baseline failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    assert result is not None
    print("PASS: Phase 10 rule baseline built and evaluated")
    print(f"Known-fault test F1: {result['metrics']['known_test']['f1']:.6f}")
    print(f"Sealed-unknown trigger rate: {result['metrics']['sealed_unknown']['trigger_rate']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
