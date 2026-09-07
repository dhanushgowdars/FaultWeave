from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .logging import run_id_context

CONTROL_PATH = Path(os.getenv("FAULT_CONTROL_PATH", "/faultweave-control/active_fault.json"))
_DELAY_SECONDS = {"mild": 0.25, "medium": 0.75, "high": 1.5}


def active_fault() -> dict | None:
    try:
        payload = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None
    if expires_at <= datetime.now(UTC) or payload.get("run_id") != run_id_context.get():
        return None
    return payload


async def apply_database_latency(service: str) -> None:
    fault = active_fault()
    if not fault or fault.get("fault_id") != "DATABASE_HIGH_LATENCY":
        return
    if fault.get("target") != service:
        return
    await asyncio.sleep(_DELAY_SECONDS[str(fault["intensity"])])


async def apply_downstream_timeout(source: str, target: str, url: str) -> None:
    fault = active_fault()
    if not fault or fault.get("fault_id") != "DOWNSTREAM_TIMEOUT":
        return
    if fault.get("target") != f"{source}->{target}":
        return
    delay = {"mild": 5.25, "medium": 6.0, "high": 8.0}[str(fault["intensity"])]
    await asyncio.sleep(delay)
    request = httpx.Request("POST", url)
    raise httpx.ReadTimeout("controlled downstream timeout", request=request)
