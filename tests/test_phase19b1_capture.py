"""Regression checks for create-only observable feature capture, without live services."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.phase19b1_capture import (
    capture_if_enabled,
    persist_observable_capture,
)
from datasets.final_features import FEATURE_NAMES


def observation() -> dict:
    return {
        "window_seconds": 60,
        "window_started_at": "2026-10-11T05:29:00Z",
        "window_ended_at": "2026-10-11T05:30:00Z",
        "coverage_seconds": 65.680836,
        "request_count": 3,
        "event_count": 7,
        "request_observation_source": "client_observed",
        "features": {name: 0.0 for name in FEATURE_NAMES},
        "scenario": {"fault_id": "MUST_NOT_LEAK"},
        "hidden_fault_label": "MUST_NOT_LEAK",
    }


def result() -> dict:
    return {
        "status": "ABNORMAL",
        "classification": {
            "label": "UNKNOWN ABNORMAL PATTERN",
            "known_class_candidate": "DATABASE_HIGH_LATENCY",
            "class_support_score": 4916.66666667,
            "class_support_limit": 10.8173424,
            "accepted_as_known": False,
        },
        "ground_truth": "MUST_NOT_LEAK",
    }


def test_disabled_by_default_creates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAULTWEAVE_PHASE19B1_CAPTURE", raising=False)
    assert capture_if_enabled(observation(), result(), FEATURE_NAMES) is None


def test_persists_only_safe_fields_and_never_overwrites(tmp_path: Path) -> None:
    first = persist_observable_capture(observation(), result(), FEATURE_NAMES, root=tmp_path)
    second = persist_observable_capture(observation(), result(), FEATURE_NAMES, root=tmp_path)
    assert first != second
    saved = json.loads(first.read_text(encoding="utf-8"))
    assert set(saved["snapshot"]["features"]) == set(FEATURE_NAMES)
    assert list(saved["snapshot"]["features"]) == list(FEATURE_NAMES)
    assert saved["observed_model"]["class_support_score"] == 4916.66666667
    assert saved["observed_model"]["known_class_candidate"] == "DATABASE_HIGH_LATENCY"
    assert "MUST_NOT_LEAK" not in first.read_text(encoding="utf-8")
    assert len(list(tmp_path.glob("observable-*.json"))) == 2


def test_incompatible_vector_is_rejected(tmp_path: Path) -> None:
    sample = observation()
    sample["features"].pop("event_count")
    with pytest.raises(ValueError, match="incompatible"):
        persist_observable_capture(sample, result(), FEATURE_NAMES, root=tmp_path)
    assert not list(tmp_path.iterdir())


def test_rejects_non_client_and_wrong_window(tmp_path: Path) -> None:
    for change in ({"window_seconds": 10}, {"request_observation_source": "gateway_derived"}):
        sample = observation()
        sample.update(change)
        with pytest.raises(ValueError, match="60-second"):
            persist_observable_capture(sample, result(), FEATURE_NAMES, root=tmp_path)
    assert not list(tmp_path.iterdir())
