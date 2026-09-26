from __future__ import annotations

import asyncio
import json

from app.intelligence_stream import (
    IntelligenceStreamMessage,
    LiveIntelligenceStream,
    encode_snapshot,
    encode_sse,
)


def test_sse_encoding_uses_id_event_and_json_data() -> None:
    message = IntelligenceStreamMessage(
        event_id=7,
        event="incident.updated",
        data={"state": "ACTIVE", "severity": {"level": "HIGH"}},
    )

    encoded = encode_sse(message)

    assert encoded.startswith("id: 7\nevent: incident.updated\ndata: ")
    payload = encoded.split("data: ", 1)[1].strip()
    assert json.loads(payload) == message.data


def test_stream_publishes_to_subscriber_and_tracks_latest() -> None:
    async def scenario() -> None:
        stream = LiveIntelligenceStream()
        token, queue = stream.subscribe()
        try:
            published = stream.publish(
                "incident.opened",
                {"incident": {"incident_id": "inc-1"}},
            )
            received = await asyncio.wait_for(queue.get(), timeout=0.1)
            assert received == published
            assert stream.latest() == published
            assert stream.current_event_id() == 1
            assert stream.subscriber_count() == 1
        finally:
            stream.unsubscribe(token)
        assert stream.subscriber_count() == 0

    asyncio.run(scenario())


def test_slow_subscriber_keeps_newest_bounded_event() -> None:
    async def scenario() -> None:
        stream = LiveIntelligenceStream(queue_size=1)
        token, queue = stream.subscribe()
        try:
            stream.publish("incident.updated", {"version": 1})
            newest = stream.publish("incident.updated", {"version": 2})
            received = await asyncio.wait_for(queue.get(), timeout=0.1)
            assert received == newest
        finally:
            stream.unsubscribe(token)

    asyncio.run(scenario())


def test_snapshot_contains_current_incident_and_latest_event() -> None:
    latest = IntelligenceStreamMessage(
        event_id=3,
        event="incident.updated",
        data={"status": "INCIDENT_UPDATED"},
    )

    encoded = encode_snapshot(
        event_id=3,
        active_incident={"incident_id": "inc-1", "state": "ACTIVE"},
        latest=latest,
    )

    assert "event: snapshot" in encoded
    payload = json.loads(encoded.split("data: ", 1)[1].strip())
    assert payload["active_incident"]["incident_id"] == "inc-1"
    assert payload["latest_event"]["event_id"] == 3
