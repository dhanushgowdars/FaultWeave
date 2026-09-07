from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.contracts import FinalDatasetPlan  # noqa: E402
from datasets.final_plan import DEFAULT_OUTPUT, validate_final_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 6 final dataset plan")
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        plan = FinalDatasetPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
        errors = validate_final_plan(plan)
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        print("FAIL: Phase 6 final-plan verification failed")
        for error in errors:
            print(f"- {error}")
        return 1
    counts = Counter(run.scenario_type for run in plan.runs)
    splits = Counter(run.split for run in plan.runs)
    print("PASS: Phase 6A final-dataset contract verified")
    print(f"Runs: {len(plan.runs)}; scenarios: {dict(counts)}")
    print(f"Whole-run splits: {dict(splits)}")
    print("Sealed unknown training/tuning eligibility: forbidden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
