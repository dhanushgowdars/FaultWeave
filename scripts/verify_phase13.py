from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.incident_impact import validate_incident_impact_report  # noqa: E402
from datasets.isolation_forest_model import validate_isolation_forest_artifacts  # noqa: E402


def main() -> int:
    report_path = DATASET_ROOT / "impact" / "report.json"
    temporal_root = DATASET_ROOT / "models" / "rich-v2-10s" / "temporal-isolation-forest"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        errors = [
            *validate_isolation_forest_artifacts(temporal_root),
            *validate_incident_impact_report(report),
        ]
    except (OSError, ValueError) as exc:
        print(f"FAIL: Phase 13 verifier could not read artifacts\n- {exc}", file=sys.stderr)
        return 1
    if errors:
        print("FAIL: Phase 13 artifact contract failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    metrics = report["metrics"]
    print("PASS: Phase 13 incident-impact artifact contract verified")
    print(f"Runs: {metrics['overall']['runs']}")
    print(f"Detection rate: {metrics['overall']['detection_rate']:.6f}")
    print(f"Median delay: {metrics['overall']['median_detection_delay_seconds']}")
    print(f"P95 delay: {metrics['overall']['p95_detection_delay_seconds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
