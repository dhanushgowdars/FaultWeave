from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any

MAX_STREAM_SUBSCRIBERS = 50
SUBSCRIBER_QUEUE_SIZE = 32
STREAM_HEARTBEAT_SECONDS = 15.0


@dataclass(frozen=True)
class IntelligenceStreamMessage:
    event_id: int
    event: str
    data: dict[str, Any]


def encode_sse(message: IntelligenceStreamMessage) -> str:
    payload = json.dumps(
        message.data,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        f"id: {message.event_id}\n"
        f"event: {message.event}\n"
        f"data: {payload}\n\n"
    )


def encode_snapshot(
    *,
    event_id: int,
    active_incident: dict[str, Any] | None,
    latest: IntelligenceStreamMessage | None,
) -> str:
    return encode_sse(
        IntelligenceStreamMessage(
            event_id=event_id,
            event="snapshot",
            data={
                "active_incident": deepcopy(active_incident),
                "latest_event": (
                    {
                        "event_id": latest.event_id,
                        "event": latest.event,
                        "data": deepcopy(latest.data),
                    }
                    if latest is not None
                    else None
                ),
            },
        )
    )


class LiveIntelligenceStream:
    def __init__(
        self,
        *,
        max_subscribers: int = MAX_STREAM_SUBSCRIBERS,
        queue_size: int = SUBSCRIBER_QUEUE_SIZE,
    ) -> None:
        if max_subscribers <= 0 or queue_size <= 0:
            raise ValueError("stream limits must be positive")
        self._max_subscribers = max_subscribers
        self._queue_size = queue_size
        self._sequence = 0
        self._subscriber_sequence = 0
        self._subscribers: dict[int, asyncio.Queue[IntelligenceStreamMessage]] = {}
        self._latest: IntelligenceStreamMessage | None = None
        self._lock = RLock()

    def subscribe(self) -> tuple[int, asyncio.Queue[IntelligenceStreamMessage]]:
        with self._lock:
            if len(self._subscribers) >= self._max_subscribers:
                raise RuntimeError("live intelligence stream subscriber limit reached")
            self._subscriber_sequence += 1
            token = self._subscriber_sequence
            queue: asyncio.Queue[IntelligenceStreamMessage] = asyncio.Queue(
                maxsize=self._queue_size
            )
            self._subscribers[token] = queue
            return token, queue

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def publish(self, event: str, data: dict[str, Any]) -> IntelligenceStreamMessage:
        if not event.strip():
            raise ValueError("stream event name cannot be empty")
        with self._lock:
            self._sequence += 1
            message = IntelligenceStreamMessage(
                event_id=self._sequence,
                event=event,
                data=deepcopy(data),
            )
            self._latest = message
            queues = tuple(self._subscribers.values())

        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass
        return message

    def latest(self) -> IntelligenceStreamMessage | None:
        with self._lock:
            return deepcopy(self._latest)

    def current_event_id(self) -> int:
        with self._lock:
            return self._sequence

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


_live_intelligence_stream = LiveIntelligenceStream()


def get_live_intelligence_stream() -> LiveIntelligenceStream:
    return _live_intelligence_stream
