from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.open_set_rejection import validate_open_set_artifacts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 11 open-set artifacts")
    parser.add_argument(
        "--root",
        type=Path,
        default=DATASET_ROOT / "models" / "open-set-rejection",
    )
    args = parser.parse_args()
    errors = validate_open_set_artifacts(args.root)
    if errors:
        print("FAIL: Phase 11 open-set verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PASS: Phase 11 open-set artifact contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
