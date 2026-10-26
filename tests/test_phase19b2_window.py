"""Phase 19B.2 safety tests; do not run real services or infer protected ground truth."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncio
import pytest
from app.phase19b2_window import (
    ChallengeWindowNotReady,
    challenge_only_snapshot,
    infer_observable,
    timestamp,
)


class FakeBuffer:
    def __init__(self, events=30, requests=12, watermark=None):
        self.calls = []
        self.events = events
        self.requests = requests
        self.watermark = watermark or datetime(2026, 1, 1, 0, 1, 10, tzinfo=UTC)

    def snapshot(self, seconds, *, require_client_requests):
        assert seconds == 10 and require_client_requests is True
        return {"window_ended_at": self.watermark.isoformat()}

    def snapshot_ending_at(self, seconds, end, *, require_client_requests):
        assert end <= self.watermark, "would attempt to read beyond actual watermark"
        self.calls.append((seconds, end, require_client_requests))
        start = end - timedelta(seconds=seconds)
        return {
            "window_seconds": seconds,
            "window_started_at": start.isoformat(),
            "window_ended_at": end.isoformat(),
            "request_observation_source": "client_observed",
            "request_count": self.requests,
            "event_count": self.events,
            "features": {"a": 1.0},
        }


def phase(duration):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return {"phase": "challenge", "started_at": start.isoformat(),
            "ended_at": (start + timedelta(seconds=duration)).isoformat(),
            "fault_id": "HIDDEN_AND_MUST_NEVER_BE_USED"}


def test_rejects_old_30_second_challenge():
    buffer = FakeBuffer()
    with pytest.raises(ChallengeWindowNotReady, match="less than 61 seconds"):
        asyncio.run(challenge_only_snapshot(buffer, phase(30), 0.1))
    assert not buffer.calls


def test_selects_only_the_challenge_window():
    buffer = FakeBuffer()
    item = asyncio.run(challenge_only_snapshot(buffer, phase(70), 0.1))
    assert item["request_count"] == 12
    length, end, explicit = buffer.calls[0]
    assert length == 60 and explicit is True
    assert end == timestamp(phase(70)["ended_at"]) - timedelta(seconds=1)
    assert timestamp(item["window_started_at"]) > timestamp(phase(70)["started_at"])
    assert timestamp(item["window_ended_at"]) < timestamp(phase(70)["ended_at"])


def test_rejects_no_real_events():
    buffer = FakeBuffer(events=0)
    with pytest.raises(ChallengeWindowNotReady, match="no genuine"):
        asyncio.run(challenge_only_snapshot(buffer, phase(70), 0.04))


def test_must_not_accept_wrong_phase():
    from app.phase19b2_window import timestamp
    with pytest.raises(ChallengeWindowNotReady):
        timestamp("2026-01-01T01:00:00")


def test_inference_does_not_disclose_input_features(monkeypatch):
    import app.phase19b2_window as target
    seen = {}
    def fake_infer(artifacts, request):
        seen["features"] = request.features
        return {"status": "ABNORMAL", "classification": {"label": "UNKNOWN ABNORMAL PATTERN"}}
    monkeypatch.setattr(target, "run_intelligence_inference", fake_infer)
    monkeypatch.setattr(target, "capture_if_enabled", lambda *args: None)
    snapshot = FakeBuffer().snapshot_ending_at(60, datetime(2026, 1, 1, tzinfo=UTC), require_client_requests=True)
    model = SimpleNamespace(feature_names=("a",))
    channel = infer_observable(snapshot, model)
    assert seen["features"] == {"a": 1.0}
    assert channel["scope"] == "challenge_only_telemetry"
    assert "features" not in channel["window"]
    assert "fault_id" not in repr(channel)


def test_lagging_watermark_selects_complete_earlier_challenge_window():
    start = timestamp(phase(70)["started_at"])
    buffer = FakeBuffer(watermark=start + timedelta(seconds=65))
    snapshot = asyncio.run(challenge_only_snapshot(buffer, phase(70), 0.1))
    assert timestamp(snapshot["window_started_at"]) == start + timedelta(seconds=5)
    assert timestamp(snapshot["window_ended_at"]) == start + timedelta(seconds=65)
    assert buffer.calls == [(60, start + timedelta(seconds=65), True)]


def test_insufficient_watermark_fails_closed_without_truncated_window():
    start = timestamp(phase(70)["started_at"])
    buffer = FakeBuffer(watermark=start + timedelta(seconds=59))
    with pytest.raises(ChallengeWindowNotReady, match="less than 60 seconds"):
        asyncio.run(challenge_only_snapshot(buffer, phase(70), 0.04))
    assert buffer.calls == []


def test_watermark_after_challenge_never_includes_recovery():
    start = timestamp(phase(70)["started_at"])
    buffer = FakeBuffer(watermark=start + timedelta(seconds=90))
    snapshot = asyncio.run(challenge_only_snapshot(buffer, phase(70), 0.1))
    assert timestamp(snapshot["window_ended_at"]) == start + timedelta(seconds=69)


def test_reaches_valid_watermark_after_bridge_catches_up():
    start = timestamp(phase(70)["started_at"])
    class DelayedBuffer(FakeBuffer):
        reads = 0
        def snapshot(self, seconds, *, require_client_requests):
            self.reads += 1
            self.watermark = start + timedelta(seconds=59 if self.reads == 1 else 66)
            return super().snapshot(seconds, require_client_requests=require_client_requests)

    buffer = DelayedBuffer()
    snapshot = asyncio.run(challenge_only_snapshot(buffer, phase(70), 0.7))
    assert buffer.reads >= 2
    assert timestamp(snapshot["window_ended_at"]) == start + timedelta(seconds=66)
