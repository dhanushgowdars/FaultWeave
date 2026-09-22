from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from datasets.final_executor import DATASET_ROOT  # noqa: E402
from datasets.final_features import (  # noqa: E402
    FEATURE_NAMES,
    PROTECTED_FIELDS,
    WINDOW_SECONDS,
    validate_feature_report,
)
from experiments.manifest import sha256_file  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def verify_features(
    root: Path = DATASET_ROOT / "features",
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        schema = json.loads((root / "feature-schema.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"unable to read feature artifacts: {exc}"]
    errors.extend(validate_feature_report(report))
    if tuple(schema.get("feature_names", [])) != FEATURE_NAMES:
        errors.append("feature schema does not match the frozen feature list")
    if tuple(schema.get("protected_fields", [])) != PROTECTED_FIELDS:
        errors.append("feature schema does not declare all protected fields")
    for seconds in WINDOW_SECONDS:
        path = root / f"windows-{seconds}s.jsonl"
        try:
            rows = read_jsonl(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{seconds}s feature rows are unreadable: {exc}")
            continue
        output = report["outputs"][str(seconds)]
        if sha256_file(path) != output["sha256"]:
            errors.append(f"{seconds}s feature checksum mismatch")
        if len(rows) != output["record_count"]:
            errors.append(f"{seconds}s feature record count mismatch")
        run_windows: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            features = row.get("features")
            if not isinstance(features, dict) or set(features) != set(FEATURE_NAMES):
                errors.append(f"{seconds}s row has a missing or changed feature schema")
                break
            if set(features).intersection(PROTECTED_FIELDS):
                errors.append(f"{seconds}s protected metadata leaked into feature values")
                break
            if row.get("scenario_type") == "sealed_unknown" and (
                row.get("training_eligible") or row.get("threshold_tuning_eligible")
            ):
                errors.append("sealed unknown feature row is eligible for training or tuning")
                break
            run_windows.setdefault(str(row.get("run_id")), []).append(row)
        if len(run_windows) != 280:
            errors.append(f"{seconds}s output does not preserve all 280 whole-run identities")
        for run_id, items in run_windows.items():
            if len({item.get("split") for item in items}) != 1:
                errors.append(f"{seconds}s {run_id} crosses a split boundary")
                break
        labels = Counter(str(item.get("label")) for item in rows)
        if labels["NORMAL"] <= 0 or len(labels) < 10:
            errors.append(f"{seconds}s output lacks normal or known-fault labels")
    return report, list(dict.fromkeys(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 7 feature-engineering artifacts")
    parser.add_argument("--root", type=Path, default=DATASET_ROOT / "features")
    args = parser.parse_args()
    report, errors = verify_features(args.root)
    if errors:
        print("FAIL: Phase 7 feature verification failed", file=sys.stderr)
        for error in errors[:40]:
            print(f"- {error}", file=sys.stderr)
        return 1
    assert report is not None
    print("PASS: Phase 7 feature-engineering contract verified")
    print(f"Runs: {report['manifest_count']}")
    print("Protected labels and identifiers: excluded from feature vectors")
    print("Sealed unknown rows: evaluation-only")
    for seconds, item in report["outputs"].items():
        print(f"{seconds}s windows: {item['record_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
