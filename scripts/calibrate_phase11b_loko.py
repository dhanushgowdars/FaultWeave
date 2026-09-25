from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.open_set_loko import calibrate_loko_policy, validate_loko_artifacts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate Phase 11B open-set policy")
    parser.add_argument(
        "--features",
        type=Path,
        default=DATASET_ROOT / "features" / "windows-30s.jsonl",
    )
    parser.add_argument(
        "--isolation-root",
        type=Path,
        default=DATASET_ROOT / "models" / "isolation-forest",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATASET_ROOT / "models" / "open-set-loko",
    )
    args = parser.parse_args()
    try:
        result = calibrate_loko_policy(args.features, args.isolation_root, args.output)
        errors = validate_loko_artifacts(args.output)
    except (OSError, ValueError) as exc:
        result, errors = None, [str(exc)]
    if errors:
        print("FAIL: Phase 11B leave-one-known-fault-out calibration failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    assert result is not None
    print("PASS: Phase 11B leave-one-known-fault-out policy calibrated")
    print(f"Synthetic-unknown F1: {result['report']['metrics']['synthetic_unknown_f1']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
