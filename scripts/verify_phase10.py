from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.rule_baseline import validate_rule_baseline_artifacts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 10 baseline artifacts")
    parser.add_argument("--root", type=Path, default=DATASET_ROOT / "models" / "rule-baseline")
    args = parser.parse_args()
    errors = validate_rule_baseline_artifacts(args.root)
    if errors:
        print("FAIL: Phase 10 baseline verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PASS: Phase 10 rule baseline artifact contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
