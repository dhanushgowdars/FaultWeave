from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.xgboost_classifier import (  # noqa: E402
    build_xgboost_classifier,
    validate_xgboost_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Phase 9 XGBoost known-fault classifier")
    parser.add_argument(
        "--features", type=Path, default=DATASET_ROOT / "features" / "windows-30s.jsonl"
    )
    parser.add_argument("--report", type=Path, default=DATASET_ROOT / "features" / "report.json")
    parser.add_argument(
        "--output", type=Path, default=DATASET_ROOT / "models" / "xgboost-known-fault"
    )
    args = parser.parse_args()
    try:
        result = build_xgboost_classifier(args.features, args.output, args.report)
        errors = validate_xgboost_artifacts(args.output)
    except (OSError, ValueError) as exc:
        errors, result = [str(exc)], None
    if errors:
        print("FAIL: Phase 9 XGBoost training failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    assert result is not None
    print("PASS: Phase 9 XGBoost classifier trained and evaluated")
    print(f"Known classes: {len(result['metadata']['classes'])}")
    print(f"Known-fault test macro F1: {result['metrics']['known_test']['macro_f1']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
