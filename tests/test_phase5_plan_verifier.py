from datasets.smoke_plan import write_smoke_plan
from scripts.verify_phase5_plan import verify


def test_phase5_plan_verifier_accepts_generated_plan(tmp_path) -> None:
    path = tmp_path / "plan.json"
    write_smoke_plan(path)
    plan, errors = verify(path)
    assert plan is not None
    assert errors == []
