from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .logging import LogOutcome, configure_logging, reset_request_id, set_request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service: str) -> None:
        super().__init__(app)
        self.logger = configure_logging(service)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        current_request_id = request.headers.get("X-Request-ID") or str(uuid4())
        current_request_id = current_request_id.strip()[:100] or str(uuid4())
        request.state.request_id = current_request_id
        context_token = set_request_id(current_request_id)
        started_at = perf_counter()
        try:
            response = await call_next(request)
            latency_ms = round((perf_counter() - started_at) * 1000, 3)
            level = "info"
            outcome = LogOutcome.SUCCESS
            if response.status_code >= 500:
                level = "error"
                outcome = LogOutcome.FAILURE
            elif response.status_code >= 400:
                level = "warning"
                outcome = LogOutcome.FAILURE
            event_type = (
                "health_check_completed"
                if request.url.path.startswith("/health/")
                else "http_request_completed"
            )
            getattr(self.logger, level)(
                event_type,
                "HTTP request completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=latency_ms,
                outcome=outcome,
            )
            response.headers["X-Request-ID"] = current_request_id
            return response
        except Exception as exc:
            latency_ms = round((perf_counter() - started_at) * 1000, 3)
            self.logger.error(
                "http_request_failed",
                "HTTP request failed with an unhandled exception",
                method=request.method,
                path=request.url.path,
                status_code=500,
                latency_ms=latency_ms,
                outcome=LogOutcome.FAILURE,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            reset_request_id(context_token)


def request_id(request: Request) -> str:
    return request.state.request_id
