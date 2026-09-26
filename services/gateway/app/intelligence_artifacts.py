from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib

MAIN_WINDOW_SECONDS = 60
TEMPORAL_WINDOW_SECONDS = 10
EXPECTED_KNOWN_CLASS_COUNT = 9
DEFAULT_MODEL_ROOT = Path("data/datasets/final/models")


class ArtifactLoadError(RuntimeError):
    """Raised when frozen intelligence artifacts are missing or inconsistent."""


@dataclass(frozen=True)
class FrozenIntelligenceArtifacts:
    feature_names: tuple[str, ...]
    classes: tuple[str, ...]
    open_set_label: str
    main_anomaly_threshold: float
    temporal_anomaly_threshold: float
    class_support_limits: dict[str, float]
    class_support_profiles: dict[str, dict[str, list[float]]]
    main_isolation_model: Any
    main_isolation_scaler: Any
    classifier_model: Any
    temporal_isolation_model: Any
    temporal_isolation_scaler: Any

    def readiness(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "main_window_seconds": MAIN_WINDOW_SECONDS,
            "temporal_window_seconds": TEMPORAL_WINDOW_SECONDS,
            "feature_count": len(self.feature_names),
            "known_class_count": len(self.classes),
            "open_set_label": self.open_set_label,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactLoadError(f"required artifact is unavailable: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactLoadError(f"artifact metadata is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ArtifactLoadError(f"artifact metadata must be a JSON object: {path}")
    return value


def _load_joblib(path: Path) -> Any:
    try:
        return joblib.load(path)
    except (OSError, ValueError, TypeError, EOFError) as exc:
        raise ArtifactLoadError(f"unable to load frozen model artifact: {path}") from exc


def _require_feature_names(metadata: dict[str, Any], source: str) -> tuple[str, ...]:
    values = metadata.get("feature_names")
    if not isinstance(values, list) or not values or not all(isinstance(item, str) for item in values):
        raise ArtifactLoadError(f"{source} metadata has no valid feature schema")
    if len(values) != len(set(values)):
        raise ArtifactLoadError(f"{source} metadata feature schema contains duplicates")
    return tuple(values)


def _require_window(metadata: dict[str, Any], expected: int, source: str) -> None:
    if metadata.get("window_seconds") != expected:
        raise ArtifactLoadError(f"{source} must use the frozen {expected}-second window")


def _require_model_bundle(bundle: Any, source: str, required: set[str]) -> dict[str, Any]:
    if not isinstance(bundle, dict) or not required.issubset(bundle):
        missing = sorted(required - set(bundle) if isinstance(bundle, dict) else required)
        raise ArtifactLoadError(f"{source} model bundle is missing keys: {', '.join(missing)}")
    return bundle


def load_frozen_artifacts(model_root: Path) -> FrozenIntelligenceArtifacts:
    root = model_root.resolve()
    main_root = root / "rich-v2-60s"
    temporal_root = root / "rich-v2-10s" / "temporal-isolation-forest"
    isolation_root = main_root / "isolation-forest"
    classifier_root = main_root / "xgboost-known-fault"
    open_set_root = main_root / "class-conditional-open-set"

    isolation_metadata = _read_json(isolation_root / "metadata.json")
    classifier_metadata = _read_json(classifier_root / "metadata.json")
    open_set_metadata = _read_json(open_set_root / "metadata.json")
    temporal_metadata = _read_json(temporal_root / "metadata.json")

    _require_window(isolation_metadata, MAIN_WINDOW_SECONDS, "main Isolation Forest")
    _require_window(classifier_metadata, MAIN_WINDOW_SECONDS, "XGBoost classifier")
    _require_window(open_set_metadata, MAIN_WINDOW_SECONDS, "open-set policy")
    _require_window(temporal_metadata, TEMPORAL_WINDOW_SECONDS, "temporal Isolation Forest")

    feature_names = _require_feature_names(isolation_metadata, "main Isolation Forest")
    for source, metadata in (
        ("XGBoost classifier", classifier_metadata),
        ("open-set policy", open_set_metadata),
        ("temporal Isolation Forest", temporal_metadata),
    ):
        if _require_feature_names(metadata, source) != feature_names:
            raise ArtifactLoadError(f"{source} feature schema differs from the frozen main schema")

    raw_classes = classifier_metadata.get("classes")
    if not isinstance(raw_classes, list) or len(raw_classes) != EXPECTED_KNOWN_CLASS_COUNT:
        raise ArtifactLoadError("classifier metadata must declare exactly nine known fault classes")
    classes = tuple(str(item) for item in raw_classes)
    if len(set(classes)) != EXPECTED_KNOWN_CLASS_COUNT:
        raise ArtifactLoadError("classifier metadata contains duplicate known fault classes")

    support_limits_raw = open_set_metadata.get("class_support_limits")
    profiles_raw = open_set_metadata.get("class_support_profiles")
    if not isinstance(support_limits_raw, dict) or set(support_limits_raw) != set(classes):
        raise ArtifactLoadError("open-set support limits do not match classifier classes")
    if not isinstance(profiles_raw, dict) or set(profiles_raw) != set(classes):
        raise ArtifactLoadError("open-set support profiles do not match classifier classes")

    support_limits = {name: float(support_limits_raw[name]) for name in classes}
    support_profiles: dict[str, dict[str, list[float]]] = {}
    for name in classes:
        profile = profiles_raw[name]
        if not isinstance(profile, dict):
            raise ArtifactLoadError(f"open-set profile for {name} is invalid")
        centers = profile.get("centers")
        scales = profile.get("scales")
        if (
            not isinstance(centers, list)
            or not isinstance(scales, list)
            or len(centers) != len(feature_names)
            or len(scales) != len(feature_names)
        ):
            raise ArtifactLoadError(f"open-set profile for {name} does not match feature schema")
        support_profiles[name] = {
            "centers": [float(value) for value in centers],
            "scales": [float(value) for value in scales],
        }

    main_threshold = float(isolation_metadata["threshold"])
    open_set_threshold = float(open_set_metadata["anomaly_threshold"])
    if abs(main_threshold - open_set_threshold) > 1e-12:
        raise ArtifactLoadError("open-set policy anomaly threshold differs from main detector")
    temporal_threshold = float(temporal_metadata["threshold"])

    isolation_bundle = _require_model_bundle(
        _load_joblib(isolation_root / "model.joblib"),
        "main Isolation Forest",
        {"model", "scaler"},
    )
    classifier_bundle = _require_model_bundle(
        _load_joblib(classifier_root / "model.joblib"),
        "XGBoost classifier",
        {"model", "classes"},
    )
    temporal_bundle = _require_model_bundle(
        _load_joblib(temporal_root / "model.joblib"),
        "temporal Isolation Forest",
        {"model", "scaler"},
    )

    bundle_classes = tuple(str(item) for item in classifier_bundle["classes"])
    if bundle_classes != classes:
        raise ArtifactLoadError("serialized classifier classes differ from classifier metadata")

    open_set_label = str(open_set_metadata.get("open_set_label") or "")
    if not open_set_label:
        raise ArtifactLoadError("open-set label is missing from frozen metadata")

    return FrozenIntelligenceArtifacts(
        feature_names=feature_names,
        classes=classes,
        open_set_label=open_set_label,
        main_anomaly_threshold=main_threshold,
        temporal_anomaly_threshold=temporal_threshold,
        class_support_limits=support_limits,
        class_support_profiles=support_profiles,
        main_isolation_model=isolation_bundle["model"],
        main_isolation_scaler=isolation_bundle["scaler"],
        classifier_model=classifier_bundle["model"],
        temporal_isolation_model=temporal_bundle["model"],
        temporal_isolation_scaler=temporal_bundle["scaler"],
    )


@lru_cache(maxsize=1)
def get_frozen_artifacts() -> FrozenIntelligenceArtifacts:
    configured = os.getenv("FAULTWEAVE_MODEL_ROOT")
    model_root = Path(configured) if configured else DEFAULT_MODEL_ROOT
    return load_frozen_artifacts(model_root)
