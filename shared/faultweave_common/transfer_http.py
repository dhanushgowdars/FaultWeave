from __future__ import annotations

import os

import httpx

from .config import SERVICE_TOKEN
from .http import DownstreamClient
from .logging import configure_logging

# Dependency seam for integration tests; normal deployment uses the network.
transport: httpx.AsyncBaseTransport | None = None


async def call_service(source, target, path, authorization, payload=None, method="POST"):
    env_name = f"{target.upper()}_SERVICE_URL"
    default_host = "auth-service" if target == "authentication" else f"{target}-service"
    if target == "authentication":
        env_name = "AUTH_SERVICE_URL"
    url = os.getenv(env_name, f"http://{default_host}:8000") + path
    headers = {"X-Service-Token": SERVICE_TOKEN}
    if authorization:
        headers["Authorization"] = authorization
    downstream = DownstreamClient(configure_logging(source), source)
    timeout = {"gateway": 60.0, "transaction": 30.0, "payment": 15.0}.get(source, 5.0)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        result = await downstream.request(
            client, method, url, target, payload=payload or {}, headers=headers
        )
    return result.json()


def failure_detail(exc, step):
    result = {"step": step, "error_type": type(exc).__name__}
    if isinstance(exc, httpx.HTTPStatusError):
        result["http_status"] = exc.response.status_code
        try:
            result["detail"] = str(
                exc.response.json().get("detail", "Dependency rejected request")
            )[:500]
        except ValueError:
            result["detail"] = "Dependency returned a non-JSON error"
    elif isinstance(exc, ValueError):
        result["detail"] = "Dependency returned an invalid or mismatched confirmation"
    else:
        result["detail"] = "Dependency response unavailable; inspect evidence before retrying"
    return result
