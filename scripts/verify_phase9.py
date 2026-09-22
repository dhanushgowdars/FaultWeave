from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.xgboost_classifier import validate_xgboost_artifacts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 9 XGBoost artifacts")
    parser.add_argument(
        "--root", type=Path, default=DATASET_ROOT / "models" / "xgboost-known-fault"
    )
    args = parser.parse_args()
    errors = validate_xgboost_artifacts(args.root)
    if errors:
        print("FAIL: Phase 9 XGBoost verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PASS: Phase 9 XGBoost artifact contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
