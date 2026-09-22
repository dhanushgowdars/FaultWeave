from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.isolation_forest_model import (  # noqa: E402
    build_isolation_forest,
    validate_isolation_forest_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the Phase 8 Isolation Forest baseline")
    parser.add_argument(
        "--features", type=Path, default=DATASET_ROOT / "features" / "windows-30s.jsonl"
    )
    parser.add_argument(
        "--feature-report", type=Path, default=DATASET_ROOT / "features" / "report.json"
    )
    parser.add_argument("--output", type=Path, default=DATASET_ROOT / "models" / "isolation-forest")
    args = parser.parse_args()
    try:
        result = build_isolation_forest(args.features, args.output, args.feature_report)
        errors = validate_isolation_forest_artifacts(args.output)
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
        result = None
    if errors:
        print("FAIL: Phase 8 Isolation Forest training failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    assert result is not None
    print("PASS: Phase 8 Isolation Forest trained and evaluated")
    print(f"Normal training windows: {result['metadata']['training']['rows']}")
    print(f"Threshold: {result['metadata']['threshold']:.6f}")
    print(f"Known-fault test F1: {result['metrics']['known_test']['f1']:.6f}")
    unknown_rate = result["metrics"]["sealed_unknown"]["detected_anomaly_rate"]
    print(f"Sealed-unknown detection rate: {unknown_rate:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
