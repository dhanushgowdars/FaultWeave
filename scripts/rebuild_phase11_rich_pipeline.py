from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.final_features import (  # noqa: E402
    build_final_feature_dataset,
    validate_feature_report,
)
from datasets.final_plan import DEFAULT_OUTPUT  # noqa: E402
from datasets.isolation_forest_model import (  # noqa: E402
    build_isolation_forest,
    validate_isolation_forest_artifacts,
)
from datasets.open_set_loko import calibrate_loko_policy, validate_loko_artifacts  # noqa: E402
from datasets.open_set_rejection import (  # noqa: E402
    build_open_set_rejection,
    validate_open_set_artifacts,
)
from datasets.xgboost_classifier import (  # noqa: E402
    build_xgboost_classifier,
    validate_xgboost_artifacts,
)
from scripts.verify_phase6 import verify_final_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild FaultWeave models from full raw data using rich observable features"
    )
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-name", default="rich-v2")
    args = parser.parse_args()

    manifests, errors = verify_final_dataset(args.plan, partition="all", require_complete=True)
    if errors:
        print("FAIL: Phase 11C source dataset verification failed", file=sys.stderr)
        for error in errors[:40]:
            print(f"- {error}", file=sys.stderr)
        return 1

    feature_root = DATASET_ROOT / f"features-{args.output_name}"
    model_root = DATASET_ROOT / "models" / args.output_name
    try:
        feature_report = build_final_feature_dataset(PROJECT_DIRECTORY, manifests, feature_root)
        errors = validate_feature_report(feature_report)
        if errors:
            raise ValueError("; ".join(errors))

        feature_path = feature_root / "windows-30s.jsonl"
        feature_report_path = feature_root / "report.json"
        isolation_root = model_root / "isolation-forest"
        classifier_root = model_root / "xgboost-known-fault"
        policy_root = model_root / "open-set-loko"
        open_set_root = model_root / "open-set-rejection"

        isolation = build_isolation_forest(feature_path, isolation_root, feature_report_path)
        classifier = build_xgboost_classifier(feature_path, classifier_root, feature_report_path)
        loko = calibrate_loko_policy(feature_path, isolation_root, policy_root)
        open_set = build_open_set_rejection(
            feature_path,
            isolation_root,
            classifier_root,
            open_set_root,
            policy_root / "policy.json",
        )
        errors = [
            *validate_isolation_forest_artifacts(isolation_root),
            *validate_xgboost_artifacts(classifier_root),
            *validate_loko_artifacts(policy_root),
            *validate_open_set_artifacts(open_set_root),
        ]
    except (OSError, ValueError) as exc:
        print(f"FAIL: Phase 11C rich-feature pipeline failed\n- {exc}", file=sys.stderr)
        return 1

    if errors:
        print("FAIL: Phase 11C rich-feature artifact verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("PASS: Phase 11C full-data rich-feature pipeline completed")
    print(f"Accepted runs processed: {len(manifests)}")
    print(f"Observable feature count: {len(isolation['metadata']['feature_names'])}")
    print(f"30s windows: {feature_report['outputs']['30']['record_count']}")
    print(f"Source events assigned: {feature_report['outputs']['30']['assigned_events']}")
    print(f"Source requests assigned: {feature_report['outputs']['30']['assigned_requests']}")
    print(f"Isolation Forest known-test F1: {isolation['metrics']['known_test']['f1']:.6f}")
    print(
        "Isolation Forest sealed detection: "
        f"{isolation['metrics']['sealed_unknown']['detected_anomaly_rate']:.6f}"
    )
    print(f"XGBoost known-test macro F1: {classifier['metrics']['known_test']['macro_f1']:.6f}")
    print(f"LOKO synthetic-unknown F1: {loko['report']['metrics']['synthetic_unknown_f1']:.6f}")
    print(
        "Final known-fault accuracy: "
        f"{open_set['metrics']['known_test']['known_fault_accuracy']:.6f}"
    )
    print(
        "Final sealed-unknown rejection: "
        f"{open_set['metrics']['sealed_unknown']['unknown_rejection_rate']:.6f}"
    )
    print("Sealed unknowns were evaluation-only and were not used for fitting or tuning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
