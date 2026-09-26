from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.incident_impact import (  # noqa: E402
    build_incident_impact_report,
    validate_incident_impact_report,
)
from datasets.isolation_forest_model import (  # noqa: E402
    build_isolation_forest,
    validate_isolation_forest_artifacts,
)


def main() -> int:
    feature_root = DATASET_ROOT / "features-rich-v2"
    feature_path = feature_root / "windows-10s.jsonl"
    feature_report = feature_root / "report.json"
    temporal_root = DATASET_ROOT / "models" / "rich-v2-10s" / "temporal-isolation-forest"
    output_root = DATASET_ROOT / "impact"
    try:
        temporal = build_isolation_forest(feature_path, temporal_root, feature_report)
        errors = validate_isolation_forest_artifacts(temporal_root)
        if errors:
            raise ValueError("; ".join(errors))
        report = build_incident_impact_report(
            PROJECT_DIRECTORY,
            DATASET_ROOT,
            feature_path,
            temporal_root,
            output_root,
        )
        errors = validate_incident_impact_report(report)
    except (OSError, ValueError) as exc:
        print(f"FAIL: Phase 13 impact build failed\n- {exc}", file=sys.stderr)
        return 1
    if errors:
        print("FAIL: Phase 13 impact verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    known_test = temporal["metrics"]["known_test"]
    overall = report["metrics"]["overall"]
    known = report["metrics"]["known_fault"]
    unknown = report["metrics"]["development_unknown"]
    print("PASS: Phase 13 incident impact report built")
    print(f"10s temporal Isolation Forest known-test F1: {known_test['f1']:.6f}")
    print(
        "10s temporal normal false-positive rate: "
        f"{known_test['normal_false_positive_rate']:.6f}"
    )
    print(f"Overall detection rate: {overall['detection_rate']:.6f}")
    print(f"Known-fault detection rate: {known['detection_rate']:.6f}")
    print(f"Development-unknown detection rate: {unknown['detection_rate']:.6f}")
    print(f"Median detection delay: {overall['median_detection_delay_seconds']}")
    print(f"P95 detection delay: {overall['p95_detection_delay_seconds']}")
    print(f"Severity counts: {overall['severity_counts']}")
    print(f"Report SHA256: {report['report_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
