from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.class_conditional_open_set import (  # noqa: E402
    build_class_conditional_open_set,
    validate_class_conditional_artifacts,
)
from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.isolation_forest_model import (  # noqa: E402
    build_isolation_forest,
    validate_isolation_forest_artifacts,
)
from datasets.xgboost_classifier import (  # noqa: E402
    build_xgboost_classifier,
    validate_xgboost_artifacts,
)


def main() -> int:
    feature_root = DATASET_ROOT / "features-rich-v2"
    feature_path = feature_root / "windows-60s.jsonl"
    report_path = feature_root / "report.json"
    model_root = DATASET_ROOT / "models" / "rich-v2-60s"
    isolation_root = model_root / "isolation-forest"
    classifier_root = model_root / "xgboost-known-fault"
    open_set_root = model_root / "class-conditional-open-set"
    try:
        isolation = build_isolation_forest(feature_path, isolation_root, report_path)
        classifier = build_xgboost_classifier(feature_path, classifier_root, report_path)
        result = build_class_conditional_open_set(
            feature_path,
            isolation_root,
            classifier_root,
            open_set_root,
        )
        errors = [
            *validate_isolation_forest_artifacts(isolation_root),
            *validate_xgboost_artifacts(classifier_root),
            *validate_class_conditional_artifacts(open_set_root),
        ]
    except (OSError, ValueError) as exc:
        print(f"FAIL: Phase 11D class-conditional open-set build failed\n- {exc}", file=sys.stderr)
        return 1
    if errors:
        print("FAIL: Phase 11D artifact verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    known = result["metrics"]["known_test"]
    unknown = result["metrics"]["development_unknown"]
    print("PASS: Phase 11D class-conditional open-set model built")
    print(f"60s Isolation Forest known-test F1: {isolation['metrics']['known_test']['f1']:.6f}")
    print(f"60s XGBoost known-test macro F1: {classifier['metrics']['known_test']['macro_f1']:.6f}")
    print(f"Final known-fault accuracy: {known['known_fault_accuracy']:.6f}")
    print(f"Normal accuracy: {known['normal_accuracy']:.6f}")
    print(f"Development-unknown rejection: {unknown['unknown_rejection_rate']:.6f}")
    for family, metrics in unknown["families"].items():
        print(f"{family}: {metrics['unknown_rejection_rate']:.6f}")
    print("No development-unknown row was used for fitting or threshold calibration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
