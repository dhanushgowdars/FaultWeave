from datetime import UTC, datetime

from datasets.smoke_plan import build_smoke_plan
from scripts.verify_phase5 import verify_dataset


def test_empty_dataset_fails_complete_verification(tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        build_smoke_plan(datetime(2026, 1, 1, tzinfo=UTC)).model_dump_json(),
        encoding="utf-8",
    )
    manifests, errors = verify_dataset(plan_path, tmp_path, require_complete=True)
    assert manifests == []
    assert any("missing 55" in error for error in errors)
