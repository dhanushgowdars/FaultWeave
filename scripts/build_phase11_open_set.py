from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.open_set_rejection import (  # noqa: E402
    build_open_set_rejection,
    validate_open_set_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Phase 11 open-set rejection artifacts")
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
        "--classifier-root",
        type=Path,
        default=DATASET_ROOT / "models" / "xgboost-known-fault",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATASET_ROOT / "models" / "open-set-rejection",
    )
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args()
    try:
        result = build_open_set_rejection(
            args.features,
            args.isolation_root,
            args.classifier_root,
            args.output,
            args.policy,
        )
        errors = validate_open_set_artifacts(args.output)
    except (OSError, ValueError) as exc:
        result, errors = None, [str(exc)]
    if errors:
        print("FAIL: Phase 11 open-set rejection failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    assert result is not None
    print("PASS: Phase 11 open-set rejection built and evaluated")
    print(f"Known-fault accuracy: {result['metrics']['known_test']['known_fault_accuracy']:.6f}")
    print(
        "Sealed-unknown rejection rate: "
        f"{result['metrics']['sealed_unknown']['unknown_rejection_rate']:.6f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
