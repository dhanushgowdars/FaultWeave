"""Bounded challenge-only v1 inference; never uses scenario labels or recovery observations."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from .intelligence_inference import IntelligenceInferenceRequest, run_intelligence_inference
from .intelligence_live import LiveWindowNotReady
from .phase19b1_capture import capture_if_enabled

WINDOW_SECONDS = 60
END_MARGIN_SECONDS = 1.0


class ChallengeWindowNotReady(RuntimeError):
    """Observable telemetry cannot prove a complete challenge-only window."""


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ChallengeWindowNotReady("phase boundary has no timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ChallengeWindowNotReady("invalid phase boundary timestamp") from exc
    if result.tzinfo is None:
        raise ChallengeWindowNotReady("phase boundary must be timezone-aware")
    return result.astimezone(UTC)


async def challenge_only_snapshot(buffer: Any, challenge: dict[str, Any], wait_seconds: float) -> dict[str, Any]:
    """Get a real 60s window that is entirely inside the recorded challenge phase.

    The end margin keeps the right boundary strictly before the end of the phase.
    Never shorten, extrapolate, scale or pad observations to 60 seconds.
    """
    if challenge.get("phase") != "challenge":
        raise ChallengeWindowNotReady("expected the challenge phase, not baseline/recovery")
    started_at = timestamp(challenge.get("started_at"))
    ended_at = timestamp(challenge.get("ended_at"))
    phase_end_limit = ended_at - timedelta(seconds=END_MARGIN_SECONDS)
    earliest_valid_end = started_at + timedelta(seconds=WINDOW_SECONDS)
    if phase_end_limit <= earliest_valid_end:
        raise ChallengeWindowNotReady(
            "challenge has less than 61 seconds of actual evidence; configure at least 70 seconds"
        )
    if wait_seconds <= 0:
        raise ChallengeWindowNotReady("telemetry bridge timeout must be positive")

    deadline = time.monotonic() + wait_seconds
    reason = "no complete 60-second challenge-only window at the observed event watermark"
    while time.monotonic() < deadline:
        try:
            # The bridge's event watermark can trail the recorded phase end.
            # Never request an event interval past that watermark or pad a short window.
            latest = buffer.snapshot(10, require_client_requests=True)
            watermark = timestamp(latest["window_ended_at"])
            selected_end = min(phase_end_limit, watermark)
            if selected_end <= earliest_valid_end:
                reason = "telemetry watermark covers less than 60 seconds inside challenge"
                await asyncio.sleep(0.25)
                continue
            snapshot = buffer.snapshot_ending_at(
                WINDOW_SECONDS, selected_end, require_client_requests=True
            )
            if (
                timestamp(snapshot["window_started_at"]) < started_at
                or timestamp(snapshot["window_ended_at"]) > ended_at
                or snapshot["window_seconds"] != WINDOW_SECONDS
            ):
                raise ChallengeWindowNotReady("selected model window crosses a phase boundary")
            if snapshot.get("request_observation_source") != "client_observed":
                raise ChallengeWindowNotReady("client-observed requests are required")
            if int(snapshot.get("event_count") or 0) <= 0 or int(snapshot.get("request_count") or 0) <= 0:
                reason = "selected challenge window has no genuine request or service events"
            elif not isinstance(snapshot.get("features"), dict):
                raise ChallengeWindowNotReady("selected window contains no features")
            else:
                return snapshot
        except LiveWindowNotReady as exc:
            reason = str(exc)
        await asyncio.sleep(0.25)
    raise ChallengeWindowNotReady(reason)


def infer_observable(snapshot: dict[str, Any], artifacts: Any) -> dict[str, Any]:
    """Compute and persist the decision before recovery; never return raw features to APIs."""
    # This function receives only observable features and the frozen model bundle.
    result = run_intelligence_inference(
        artifacts,
        IntelligenceInferenceRequest(window_seconds=WINDOW_SECONDS, features=snapshot["features"]),
    )
    try:
        capture_if_enabled(snapshot, result, artifacts.feature_names)
    except (OSError, TypeError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "Phase 19B.2 optional evidence capture unavailable: %s", type(exc).__name__
        )
    return {
        "status": "EVALUATED",
        "reason": None,
        "scope": "challenge_only_telemetry",
        "window": {k: v for k, v in snapshot.items() if k != "features"},
        "feature_schema_sha256": hashlib.sha256(
            "\n".join(artifacts.feature_names).encode("utf-8")
        ).hexdigest(),
        "result": result,
    }
