from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .intelligence_artifacts import FrozenIntelligenceArtifacts


class InferenceInputError(ValueError):
    """Raised when a live inference request does not match the frozen feature contract."""


class IntelligenceInferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_seconds: Literal[10, 60]
    features: dict[str, float]


def _feature_vector(
    artifacts: FrozenIntelligenceArtifacts,
    features: dict[str, float],
) -> tuple[float, ...]:
    expected = set(artifacts.feature_names)
    supplied = set(features)
    if supplied != expected:
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if extra:
            details.append(f"extra={','.join(extra)}")
        raise InferenceInputError(
            "feature payload does not match frozen schema"
            + (f" ({'; '.join(details)})" if details else "")
        )
    vector = tuple(float(features[name]) for name in artifacts.feature_names)
    if any(not math.isfinite(value) for value in vector):
        raise InferenceInputError("feature payload contains a non-finite value")
    return vector


def _anomaly_score(model: Any, scaler: Any, vector: tuple[float, ...]) -> float:
    transformed = scaler.transform([vector])
    scores = model.decision_function(transformed)
    if len(scores) != 1:
        raise RuntimeError("frozen anomaly detector returned an unexpected score shape")
    return -float(scores[0])


def _support_score(
    vector: tuple[float, ...],
    profile: dict[str, list[float]],
) -> float:
    centers = profile["centers"]
    scales = profile["scales"]
    if len(centers) != len(vector) or len(scales) != len(vector):
        raise RuntimeError("frozen class-support profile does not match feature vector")
    if any(scale <= 0 or not math.isfinite(scale) for scale in scales):
        raise RuntimeError("frozen class-support profile contains an invalid scale")
    return max(
        abs(value - center) / scale
        for value, center, scale in zip(vector, centers, scales, strict=True)
    )


def _temporal_result(
    artifacts: FrozenIntelligenceArtifacts,
    vector: tuple[float, ...],
) -> dict[str, Any]:
    score = _anomaly_score(
        artifacts.temporal_isolation_model,
        artifacts.temporal_isolation_scaler,
        vector,
    )
    threshold = artifacts.temporal_anomaly_threshold
    anomalous = score >= threshold
    return {
        "window_seconds": 10,
        "pipeline": "temporal_detection",
        "status": "ABNORMAL_SIGNAL" if anomalous else "NORMAL",
        "anomaly": {
            "score": round(score, 8),
            "threshold": round(threshold, 8),
            "is_anomalous": anomalous,
        },
        "classification": None,
    }


def _main_result(
    artifacts: FrozenIntelligenceArtifacts,
    vector: tuple[float, ...],
) -> dict[str, Any]:
    score = _anomaly_score(
        artifacts.main_isolation_model,
        artifacts.main_isolation_scaler,
        vector,
    )
    threshold = artifacts.main_anomaly_threshold
    anomalous = score >= threshold
    anomaly = {
        "score": round(score, 8),
        "threshold": round(threshold, 8),
        "is_anomalous": anomalous,
    }
    if not anomalous:
        return {
            "window_seconds": 60,
            "pipeline": "main_open_set",
            "status": "NORMAL",
            "anomaly": anomaly,
            "classification": {
                "label": "NORMAL",
                "known_class_candidate": None,
                "confidence": None,
                "class_support_score": None,
                "class_support_limit": None,
                "accepted_as_known": None,
            },
        }

    probabilities = artifacts.classifier_model.predict_proba([vector])
    if len(probabilities) != 1 or len(probabilities[0]) != len(artifacts.classes):
        raise RuntimeError("frozen classifier returned an unexpected probability shape")
    row = [float(value) for value in probabilities[0]]
    class_index = max(range(len(row)), key=row.__getitem__)
    class_name = artifacts.classes[class_index]
    confidence = row[class_index]
    support_score = _support_score(vector, artifacts.class_support_profiles[class_name])
    support_limit = float(artifacts.class_support_limits[class_name])
    accepted = support_score <= support_limit
    label = class_name if accepted else artifacts.open_set_label
    return {
        "window_seconds": 60,
        "pipeline": "main_open_set",
        "status": "ABNORMAL",
        "anomaly": anomaly,
        "classification": {
            "label": label,
            "known_class_candidate": class_name,
            "confidence": round(confidence, 8),
            "class_support_score": round(support_score, 8),
            "class_support_limit": round(support_limit, 8),
            "accepted_as_known": accepted,
        },
    }


def run_intelligence_inference(
    artifacts: FrozenIntelligenceArtifacts,
    request: IntelligenceInferenceRequest,
) -> dict[str, Any]:
    vector = _feature_vector(artifacts, request.features)
    if request.window_seconds == 10:
        return _temporal_result(artifacts, vector)
    return _main_result(artifacts, vector)
