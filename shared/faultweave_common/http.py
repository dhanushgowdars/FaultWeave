from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx

from .fault_injection import apply_downstream_timeout
from .logging import EventLogger, LogOutcome, correlation_headers


class DownstreamClient:
    """HTTP client wrapper that preserves correlation and dependency evidence."""

    def __init__(
        self,
        logger: EventLogger,
        source_service: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.logger = logger
        self.source_service = source_service
        self.transport = transport

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        downstream_service: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        outgoing_headers = correlation_headers()
        outgoing_headers.update(headers or {})
        started_at = perf_counter()
        try:
            await apply_downstream_timeout(self.source_service, downstream_service, url)
            response = await client.request(
                method,
                url,
                json=payload,
                headers=outgoing_headers,
            )
        except httpx.RequestError as exc:
            self.logger.error(
                "downstream_request_failed",
                "Downstream service request failed before receiving a response",
                method=method,
                path=httpx.URL(url).path,
                latency_ms=round((perf_counter() - started_at) * 1000, 3),
                outcome=LogOutcome.FAILURE,
                downstream_service=downstream_service,
                error_type=type(exc).__name__,
            )
            raise

        outcome = LogOutcome.SUCCESS if response.is_success else LogOutcome.FAILURE
        log_method = self.logger.info
        if response.status_code >= 500:
            log_method = self.logger.error
        elif response.status_code >= 400:
            log_method = self.logger.warning
        log_method(
            "downstream_request_completed",
            "Downstream service request completed",
            method=method,
            path=httpx.URL(url).path,
            status_code=response.status_code,
            latency_ms=round((perf_counter() - started_at) * 1000, 3),
            outcome=outcome,
            downstream_service=downstream_service,
        )
        response.raise_for_status()
        return response
