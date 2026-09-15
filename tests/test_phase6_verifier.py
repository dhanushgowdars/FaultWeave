from datetime import UTC, datetime

from datasets.final_plan import build_final_plan
from scripts.verify_phase6 import verify_final_dataset


def test_empty_final_dataset_reports_missing_eligible_runs(tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        build_final_plan(datetime(2026, 1, 1, tzinfo=UTC)).model_dump_json(), encoding="utf-8"
    )
    manifests, errors = verify_final_dataset(plan_path, tmp_path, "eligible", True)
    assert manifests == []
    assert any("missing 240" in error for error in errors)
