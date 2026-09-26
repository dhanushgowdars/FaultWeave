from __future__ import annotations

import asyncio
from typing import Any

import httpx

DEFAULT_BATCH_SIZE = 50
DEFAULT_FLUSH_INTERVAL_SECONDS = 0.25
DEFAULT_QUEUE_SIZE = 2000
POST_RETRIES = 3


def request_observation(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": str(result["request_id"]),
        "trace_id": (
            str(result["trace_id"]) if result.get("trace_id") is not None else None
        ),
        "started_at": str(result["started_at"]),
        "latency_ms": float(result["latency_ms"]),
        "status_code": result.get("status_code"),
        "expected_outcome": bool(result["expected_outcome"]),
        "transport_error": result.get("transport_error"),
        "scenario": str(result["scenario"]),
    }


class LiveRequestTelemetryPublisher:
    def __init__(
        self,
        gateway_url: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if batch_size <= 0 or flush_interval <= 0 or queue_size <= 0:
            raise ValueError("publisher limits must be positive")
        self.endpoint = (
            f"{gateway_url.rstrip('/')}/api/v1/intelligence/telemetry"
        )
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._transport = transport
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=queue_size
        )
        self._task: asyncio.Task[None] | None = None
        self.accepted_total = 0
        self.duplicate_total = 0
        self.batches_total = 0

    async def __aenter__(self) -> "LiveRequestTelemetryPublisher":
        if self._task is not None:
            raise RuntimeError("live request telemetry publisher is already running")
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def _raise_worker_failure(self) -> None:
        if self._task is None or not self._task.done():
            return
        failure = self._task.exception()
        if failure is not None:
            raise RuntimeError("live request telemetry publisher failed") from failure

    async def observe(self, result: dict[str, Any]) -> None:
        self._raise_worker_failure()
        await self._queue.put(request_observation(result))
        self._raise_worker_failure()

    async def close(self) -> None:
        if self._task is None:
            return
        if not self._task.done():
            await self._queue.put(None)
        task = self._task
        self._task = None
        await task

    async def _post_batch(
        self,
        client: httpx.AsyncClient,
        batch: list[dict[str, Any]],
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(POST_RETRIES):
            try:
                response = await client.post(
                    self.endpoint,
                    json={"requests": batch},
                )
                response.raise_for_status()
                payload = response.json()
                self.accepted_total += int(payload.get("accepted_requests", 0))
                self.duplicate_total += int(payload.get("duplicate_requests", 0))
                self.batches_total += 1
                return
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < POST_RETRIES:
                    await asyncio.sleep(0.15 * (attempt + 1))
        assert last_error is not None
        raise last_error

    async def _run(self) -> None:
        async with httpx.AsyncClient(
            timeout=10.0,
            transport=self._transport,
        ) as client:
            closing = False
            while not closing:
                first = await self._queue.get()
                if first is None:
                    break
                batch = [first]
                deadline = asyncio.get_running_loop().time() + self.flush_interval

                while len(batch) < self.batch_size:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        item = await asyncio.wait_for(
                            self._queue.get(),
                            timeout=remaining,
                        )
                    except TimeoutError:
                        break
                    if item is None:
                        closing = True
                        break
                    batch.append(item)

                await self._post_batch(client, batch)
