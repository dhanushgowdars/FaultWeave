from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from .logging import request_id_context, run_id_context

CONTROL_PATH = Path(os.getenv("FAULT_CONTROL_PATH", "/faultweave-control/active_fault.json"))
_DELAY_SECONDS = {"mild": 0.25, "medium": 0.75, "high": 1.5}
_POOL_CAPACITY = {"mild": 8, "medium": 12, "high": 15}
_POOL_LOCK = asyncio.Lock()
_HELD_CONNECTIONS: dict[str, list[AsyncConnection]] = {}
_LOCK_HOLDERS: dict[str, AsyncConnection] = {}
_LOCK_STARTUP_LOCK = asyncio.Lock()
_LEASE_POLL_SECONDS = 0.25


def _stable_fraction(value: str) -> float:
    return int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


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


async def _wait_for_fault_lease_end(fault: dict) -> None:
    """Release held resources on control-file clear, replacement, or expiry.

    A request-scoped fault lease can be cleared long before expires_at. Only an
    exact matching activation is allowed to keep database resources reserved.
    The timeout check also ensures cleanup if the controller dies abruptly.
    """
    expires_at = datetime.fromisoformat(str(fault["expires_at"]).replace("Z", "+00:00"))
    expected = {key: fault[key] for key in ("activation_id", "run_id", "fault_id", "target")}
    while True:
        remaining = (expires_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return
        try:
            active = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(active, dict) or any(active.get(key) != value for key, value in expected.items()):
            return
        await asyncio.sleep(min(_LEASE_POLL_SECONDS, remaining))


async def _release_pool_after(
    service: str, connections: list[AsyncConnection], fault: dict
) -> None:
    try:
        await _wait_for_fault_lease_end(fault)
    finally:
        await asyncio.gather(
            *(connection.close() for connection in connections), return_exceptions=True
        )
        if _HELD_CONNECTIONS.get(service) is connections:
            _HELD_CONNECTIONS.pop(service, None)


async def apply_connection_pool_exhaustion(engine: AsyncEngine, service: str) -> None:
    fault = active_fault()
    if not fault or fault.get("fault_id") != "CONNECTION_POOL_EXHAUSTION":
        return
    if fault.get("target") != service:
        return
    async with _POOL_LOCK:
        if service in _HELD_CONNECTIONS:
            return
        connections: list[AsyncConnection] = []
        try:
            for _ in range(_POOL_CAPACITY[str(fault["intensity"])]):
                connections.append(await engine.connect())
        except BaseException:
            await asyncio.gather(
                *(connection.close() for connection in connections), return_exceptions=True
            )
            raise
        _HELD_CONNECTIONS[service] = connections
        asyncio.create_task(_release_pool_after(service, connections, fault.copy()))


def should_inject_downstream_error(service: str, path: str) -> bool:
    fault = active_fault()
    if not fault or fault.get("fault_id") != "DOWNSTREAM_ERROR_BURST":
        return False
    if fault.get("target") != service or path.startswith("/health/"):
        return False
    rate = {"mild": 0.3, "medium": 0.6, "high": 0.9}[str(fault["intensity"])]
    request_id = request_id_context.get() or "missing"
    bucket = _stable_fraction(request_id)
    return bucket < rate


def should_inject_service_unavailable(service: str, path: str) -> bool:
    """Return a controlled 503 for a selected service during a demo run.

    Health endpoints remain available so Docker can distinguish an injected
    dependency outage from a crashed container. The activation is still scoped
    to one correlated run by :func:`active_fault`.
    """
    fault = active_fault()
    if not fault or fault.get("fault_id") != "SERVICE_UNAVAILABLE":
        return False
    if fault.get("target") != service or path.startswith("/health/"):
        return False
    return True


async def apply_sealed_unknown_downstream_disruption(
    source: str, target: str, url: str
) -> None:
    """Apply an evaluation-only intermittent connection failure.

    The fault identifier is kept in protected control metadata. Observable logs contain
    only the resulting connection error, which prevents direct label leakage.
    """
    fault = active_fault()
    if not fault or fault.get("experiment_scope") != "sealed_unknown_evaluation":
        return
    if fault.get("fault_id") != "INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE":
        return
    if fault.get("target") != f"{source}->{target}":
        return
    rate = {"mild": 0.25, "medium": 0.45, "high": 0.65}[str(fault["intensity"])]
    request_id = request_id_context.get() or "missing"
    if _stable_fraction(request_id) < rate:
        request = httpx.Request("POST", url)
        raise httpx.ConnectError("intermittent downstream connection failure", request=request)


async def apply_sealed_unknown_latency_jitter(service: str, path: str) -> None:
    """Apply deterministic, multi-modal latency jitter for sealed evaluation runs."""
    fault = active_fault()
    if not fault or fault.get("experiment_scope") != "sealed_unknown_evaluation":
        return
    if fault.get("fault_id") != "LATENCY_JITTER_PARTIAL_DEGRADATION":
        return
    if fault.get("target") != service or path.startswith("/health/"):
        return
    request_id = request_id_context.get() or "missing"
    bucket = _stable_fraction(request_id)
    # A mixture of unaffected, moderately delayed and heavily delayed requests creates
    # a distribution unlike the fixed known DATABASE_HIGH_LATENCY mechanism.
    bands = {
        "mild": (0.55, 0.80, 0.12, 0.55),
        "medium": (0.35, 0.72, 0.25, 0.95),
        "high": (0.20, 0.60, 0.45, 1.50),
    }
    unaffected, moderate, moderate_delay, heavy_delay = bands[str(fault["intensity"])]
    if bucket < unaffected:
        return
    delay = moderate_delay if bucket < moderate else heavy_delay
    await asyncio.sleep(delay)


async def _release_database_lock(
    service: str, connection: AsyncConnection, lock_key: int, fault: dict
) -> None:
    try:
        await _wait_for_fault_lease_end(fault)
    finally:
        # A session-level PostgreSQL advisory lock survives ordinary transaction
        # rollback; explicitly unlock even when the cleanup task is cancelled.
        try:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        finally:
            await connection.close()
            if _LOCK_HOLDERS.get(service) is connection:
                _LOCK_HOLDERS.pop(service, None)


async def apply_database_lock_contention(
    engine: AsyncEngine, session: AsyncSession, service: str
) -> None:
    fault = active_fault()
    if not fault or fault.get("fault_id") != "DATABASE_LOCK_CONTENTION":
        return
    if fault.get("target") != service:
        return
    lock_key = {"transaction": 41001, "payment": 41002, "ledger": 41003}[service]
    async with _LOCK_STARTUP_LOCK:
        if service not in _LOCK_HOLDERS:
            holder = await engine.connect()
            try:
                await holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
            except BaseException:
                await holder.close()
                raise
            _LOCK_HOLDERS[service] = holder
            asyncio.create_task(_release_database_lock(service, holder, lock_key, fault.copy()))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
