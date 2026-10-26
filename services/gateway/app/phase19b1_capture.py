"""Opt-in, create-only capture of the exact observable vector used by v1 inference.

The gateway's existing /faultweave-demo mount persists files on the host.
No source label, scenario, review, transfer identifier, or raw event is written.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

CAPTURE_ROOT = Path("/faultweave-demo/phase19b1-observable")
ENABLED_ENV = "FAULTWEAVE_PHASE19B1_CAPTURE"
SNAPSHOT_FIELDS = (
    "window_seconds",
    "window_started_at",
    "window_ended_at",
    "coverage_seconds",
    "request_count",
    "event_count",
    "request_observation_source",
)


def persist_observable_capture(
    snapshot: dict[str, Any],
    result: dict[str, Any],
    feature_names: tuple[str, ...],
    *,
    root: Path = CAPTURE_ROOT,
) -> Path:
    """Write one new diagnostic JSON, preserving the precise inference input.

    Raises if evidence cannot be captured; the caller may catch failures to
    avoid changing the inference decision or banking workflow.
    """
    features = snapshot.get("features")
    if not isinstance(features, dict) or len(feature_names) != 65:
        raise ValueError("cannot capture missing or non-v1 feature data")
    if set(features) != set(feature_names) or len(features) != len(feature_names):
        raise ValueError("cannot capture incompatible frozen features")
    clean_features: dict[str, float] = {}
    for name in feature_names:
        value = features[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"non-finite or nonnumeric observable feature: {name}")
        clean_features[name] = float(value)

    if snapshot.get("window_seconds") != 60 or snapshot.get("request_observation_source") != "client_observed":
        raise ValueError("audit only captures 60-second client-observed feature windows")
    classification = result.get("classification") or {}
    safe_model = {
        "status": result.get("status"),
        "known_class_candidate": classification.get("known_class_candidate"),
        "class_support_score": classification.get("class_support_score"),
        "class_support_limit": classification.get("class_support_limit"),
        "accepted_as_known": classification.get("accepted_as_known"),
    }
    payload = {
        "audit_capture_version": "19B.1-v1",
        "captured_at": datetime.now(UTC).isoformat(),
        "hidden_fault_label_used": False,
        "feature_schema_sha256": hashlib.sha256(
            "\n".join(feature_names).encode("utf-8")
        ).hexdigest(),
        "snapshot": {
            **{name: snapshot[name] for name in SNAPSHOT_FIELDS if name in snapshot},
            "features": clean_features,
        },
        "observed_model": safe_model,
    }
    if root.is_symlink():
        raise ValueError("refusing to capture into a symlink directory")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("refusing to capture into a symlink directory")
    path = root / f"observable-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex}.json"
    # Exclusive creation: existing evidence can never be overwritten.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def capture_if_enabled(
    snapshot: dict[str, Any], result: dict[str, Any], feature_names: tuple[str, ...]
) -> Path | None:
    """Disabled by default; caller decides whether to log capture failures."""
    if os.getenv(ENABLED_ENV) != "1":
        return None
    return persist_observable_capture(snapshot, result, feature_names)
