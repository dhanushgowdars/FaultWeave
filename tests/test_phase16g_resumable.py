from __future__ import annotations

import json

from scripts import verify_phase16g


def result(case_id: str, lifecycle: str, *, passed: bool = True) -> dict:
    return {
        "case_id": case_id,
        "lifecycle": lifecycle,
        "response": {
            "status": "AI_GENERATED" if passed else "DETERMINISTIC_FALLBACK",
            "provenance": {
                "provider": "groq",
                "model": "openai/gpt-oss-120b",
            },
        },
        "evaluation": {
            "passed": passed,
            "score": 1.0 if passed else 0.95,
            "failed_checks": [] if passed else ["real_ai_generated"],
        },
    }


def test_expected_checkpoint_matrix_contains_seven_explanations() -> None:
    keys = verify_phase16g.expected_case_keys()

    assert len(keys) == 7
    assert ("normal-transfer", "normal") in keys
    assert ("ledger-service-unavailable", "recovered") in keys


def test_checkpoint_round_trip_preserves_each_case_independently(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verify_phase16g, "CHECKPOINT_DIRECTORY", tmp_path)
    first = result("normal-transfer", "normal")
    second = result("account-response-timeout", "awaiting_review", passed=False)

    verify_phase16g.save_checkpoints([first, second])

    normal_path = tmp_path / "normal-transfer--normal.json"
    failed_path = tmp_path / "account-response-timeout--awaiting_review.json"
    assert json.loads(normal_path.read_text(encoding="utf-8")) == first
    assert json.loads(failed_path.read_text(encoding="utf-8")) == second


def test_loading_checkpoints_reports_only_missing_cases(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verify_phase16g, "CHECKPOINT_DIRECTORY", tmp_path)
    expected = [
        result(case_id, lifecycle) for case_id, lifecycle in verify_phase16g.expected_case_keys()
    ]
    verify_phase16g.save_checkpoints(expected[:-1])

    loaded, missing = verify_phase16g.load_checkpoints()

    assert len(loaded) == 6
    assert missing == ["account-error-response [awaiting_review]"]


def test_import_previous_report_keeps_only_passing_real_ai_cases(tmp_path, monkeypatch) -> None:
    checkpoint_dir = tmp_path / "checkpoints"
    monkeypatch.setattr(verify_phase16g, "CHECKPOINT_DIRECTORY", checkpoint_dir)
    report_path = tmp_path / "acceptance.json"
    report_path.write_text(
        json.dumps(
            {
                "cases": [
                    result("normal-transfer", "normal"),
                    result(
                        "account-response-timeout",
                        "awaiting_review",
                        passed=False,
                    ),
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code = verify_phase16g.import_previous_report(report_path)

    assert exit_code == 0
    assert (checkpoint_dir / "normal-transfer--normal.json").exists()
    assert not (checkpoint_dir / "account-response-timeout--awaiting_review.json").exists()
