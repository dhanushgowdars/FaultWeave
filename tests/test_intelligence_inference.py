from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.intelligence_artifacts import FrozenIntelligenceArtifacts
from app.intelligence_inference import (
    InferenceInputError,
    IntelligenceInferenceRequest,
    run_intelligence_inference,
)

FEATURES = ("feature_a", "feature_b", "feature_c")
CLASSES = tuple(f"FAULT_{index}" for index in range(9))


class IdentityScaler:
    def transform(self, rows: list[tuple[float, ...]]) -> list[tuple[float, ...]]:
        return rows


class FixedIsolationForest:
    def __init__(self, decision_score: float) -> None:
        self.decision_score = decision_score

    def decision_function(self, rows: Any) -> list[float]:
        assert len(rows) == 1
        return [self.decision_score]


class FixedClassifier:
    def __init__(self, selected: int = 0, confidence: float = 0.9) -> None:
        self.selected = selected
        self.confidence = confidence
        self.calls = 0

    def predict_proba(self, rows: Any) -> list[list[float]]:
        self.calls += 1
        assert len(rows) == 1
        remainder = (1.0 - self.confidence) / 8
        values = [remainder] * 9
        values[self.selected] = self.confidence
        return [values]


class FailingClassifier:
    def predict_proba(self, _rows: Any) -> list[list[float]]:
        raise AssertionError("classifier must not run for a normal window")


def artifacts() -> FrozenIntelligenceArtifacts:
    profiles = {
        name: {"centers": [0.0, 0.0, 0.0], "scales": [1.0, 1.0, 1.0]}
        for name in CLASSES
    }
    return FrozenIntelligenceArtifacts(
        feature_names=FEATURES,
        classes=CLASSES,
        open_set_label="UNKNOWN ABNORMAL PATTERN",
        main_anomaly_threshold=0.25,
        temporal_anomaly_threshold=0.20,
        class_support_limits={name: 2.0 for name in CLASSES},
        class_support_profiles=profiles,
        main_isolation_model=FixedIsolationForest(-0.30),
        main_isolation_scaler=IdentityScaler(),
        classifier_model=FixedClassifier(),
        temporal_isolation_model=FixedIsolationForest(-0.30),
        temporal_isolation_scaler=IdentityScaler(),
    )


def request(window_seconds: int, values: dict[str, float] | None = None) -> Any:
    return IntelligenceInferenceRequest(
        window_seconds=window_seconds,
        features=values or {"feature_a": 0.2, "feature_b": 0.3, "feature_c": 0.4},
    )


def test_rejects_feature_schema_drift() -> None:
    with pytest.raises(InferenceInputError, match="frozen schema"):
        run_intelligence_inference(
            artifacts(),
            request(60, {"feature_a": 0.2, "feature_b": 0.3}),
        )


def test_temporal_window_runs_only_anomaly_detection() -> None:
    result = run_intelligence_inference(artifacts(), request(10))

    assert result["window_seconds"] == 10
    assert result["pipeline"] == "temporal_detection"
    assert result["status"] == "ABNORMAL_SIGNAL"
    assert result["anomaly"]["is_anomalous"] is True
    assert result["classification"] is None


def test_main_normal_window_bypasses_classifier() -> None:
    frozen = replace(
        artifacts(),
        main_isolation_model=FixedIsolationForest(-0.10),
        classifier_model=FailingClassifier(),
    )

    result = run_intelligence_inference(frozen, request(60))

    assert result["status"] == "NORMAL"
    assert result["classification"]["label"] == "NORMAL"


def test_main_abnormal_window_accepts_supported_known_class() -> None:
    result = run_intelligence_inference(artifacts(), request(60))

    assert result["status"] == "ABNORMAL"
    assert result["classification"]["label"] == "FAULT_0"
    assert result["classification"]["known_class_candidate"] == "FAULT_0"
    assert result["classification"]["confidence"] == 0.9
    assert result["classification"]["accepted_as_known"] is True


def test_main_abnormal_window_rejects_out_of_support_candidate() -> None:
    result = run_intelligence_inference(
        artifacts(),
        request(60, {"feature_a": 5.0, "feature_b": 0.0, "feature_c": 0.0}),
    )

    assert result["status"] == "ABNORMAL"
    assert result["classification"]["label"] == "UNKNOWN ABNORMAL PATTERN"
    assert result["classification"]["known_class_candidate"] == "FAULT_0"
    assert result["classification"]["class_support_score"] == 5.0
    assert result["classification"]["accepted_as_known"] is False
