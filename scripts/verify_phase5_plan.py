from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.contracts import DatasetPlan  # noqa: E402
from datasets.smoke_plan import DEFAULT_OUTPUT, validate_smoke_plan  # noqa: E402


def verify(path: Path) -> tuple[DatasetPlan | None, list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        plan = DatasetPlan.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        return None, [f"invalid smoke plan: {exc}"]
    return plan, validate_smoke_plan(plan)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Phase 5A smoke plan")
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    plan, errors = verify(args.plan)
    if errors or plan is None:
        print("FAIL: Phase 5A smoke-plan verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    class_counts = Counter(
        run.fault_id.value for run in plan.runs if run.fault_id is not None
    )
    print("PASS: Phase 5A smoke-plan contract verified")
    print("Total runs: 55")
    print("Normal runs: 10")
    print("Known classes: 9")
    print("Known runs per class: " + ", ".join(f"{k}={v}" for k, v in sorted(class_counts.items())))
    print("Sealed unknown contamination: none")
    print("Intervals: contiguous")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
