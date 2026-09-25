from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.class_conditional_open_set import (  # noqa: E402
    validate_class_conditional_artifacts,
)
from datasets.final_executor import DATASET_ROOT  # noqa: E402


def main() -> int:
    root = DATASET_ROOT / "models" / "rich-v2-60s" / "class-conditional-open-set"
    errors = validate_class_conditional_artifacts(root)
    if errors:
        print("FAIL: Phase 11D artifact contract failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PASS: Phase 11D class-conditional artifact contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
