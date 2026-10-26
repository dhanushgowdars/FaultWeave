from collections import Counter

from scripts.phase19c2e_matrix import make_draft, split_for_replicate, validate_draft


def test_total_run_matrix():
    p = make_draft()
    assert p["status"] == "DRAFT_BLOCKED_NOT_APPROVED"
    assert len(p["runs"]) == 240
    assert Counter(r["split"] for r in p["runs"]) == {"train": 144, "validation": 48, "test": 48}
    assert len({r["run_id"] for r in p["runs"]}) == 240


def test_all_nine_fault_classes():
    p = make_draft()
    counts = Counter(r["fault_id"] for r in p["runs"] if r["fault_id"])
    assert len(counts) == 9
    assert set(counts.values()) == {20}


def test_every_run_blocked_and_rate_unapproved():
    assert all(not r["execution_allowed"] and not r["training_eligible"]
               and r["target_rps"] is None for r in make_draft()["runs"])


def test_run_ids_isolated():
    p = make_draft()
    ids = {split: {r["run_id"] for r in p["runs"] if r["split"] == split}
           for split in ("train", "validation", "test")}
    assert not (ids["train"] & ids["validation"] or ids["train"] & ids["test"] or ids["validation"] & ids["test"])


def test_split_boundaries():
    assert split_for_replicate(1) == "train"
    assert split_for_replicate(12) == "train"
    assert split_for_replicate(13) == "validation"
    assert split_for_replicate(16) == "validation"
    assert split_for_replicate(17) == "test"
    assert split_for_replicate(20) == "test"


def test_intensity_and_target_validated():
    p = make_draft()
    assert not validate_draft(p)


def test_stable_digest():
    assert make_draft()["draft_sha256"] == make_draft()["draft_sha256"]


def test_reject_execution():
    p = make_draft()
    p["runs"][0]["execution_allowed"] = True
    try:
        validate_draft(p)
    except ValueError as e:
        assert "unauthorized" in str(e)
    else:
        raise AssertionError("failed to block an executable run")


def test_reject_rate_guess():
    p = make_draft()
    p["runs"][0]["target_rps"] = 2.0
    try:
        validate_draft(p)
    except ValueError as e:
        assert "unauthorized" in str(e)
    else:
        raise AssertionError("failed to block unsupported rate")


def test_high_load_and_infra_blockers():
    p = make_draft()
    gates = {r["fault_id"]: r["gate"] for r in p["runs"] if r["fault_id"]}
    assert gates["HIGH_LOAD"].startswith("OVERLOAD_")
    assert gates["DATABASE_UNAVAILABLE"].startswith("EXPLICIT_")


def test_rate_target_intensity_not_perfectly_confounded():
    rows = make_draft()["runs"]
    family = [r for r in rows if r["fault_id"] == "SERVICE_UNAVAILABLE" and r["split"] == "train"]
    assert len({r["target"] for r in family if r["rate_regime"] == "low"}) > 1
    assert len({r["intensity"] for r in family if r["rate_regime"] == "low"}) > 1
