from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.incident_localization import validate_localization_report  # noqa: E402


def main() -> int:
    path = DATASET_ROOT / "localization" / "report.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_localization_report(report)
    except (OSError, ValueError) as exc:
        print(f"FAIL: Phase 12 verifier could not read artifacts\n- {exc}", file=sys.stderr)
        return 1
    if errors:
        print("FAIL: Phase 12 localization artifact contract failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    metrics = report["metrics"]
    print("PASS: Phase 12 localization artifact contract verified")
    print(f"Runs: {metrics['overall']['runs']}")
    print(f"Top-1: {metrics['overall']['top_1_accuracy']:.6f}")
    print(f"Top-3: {metrics['overall']['top_3_accuracy']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
