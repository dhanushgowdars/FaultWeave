"""Sustained, observable workload used by the sealed judge challenge.

The workload calls the original research transaction API so its traffic follows
the same six-service path used to build the frozen feature contract.  It never
moves money in the durable account-to-account transfer shown to the judge.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from .intelligence_live import (
    LiveRequestObservation,
    LiveTelemetryBuffer,
    LiveWindowNotReady,
    get_live_telemetry_buffer,
)

DEFAULT_BASELINE_SECONDS = 15.0
DEFAULT_CHALLENGE_SECONDS = 30.0
DEFAULT_RECOVERY_SECONDS = 20.0
DEFAULT_REQUESTS_PER_SECOND = 3.0
DEFAULT_MAX_CONCURRENCY = 20
MODEL_WINDOW_SECONDS = 60


class DemoTelemetryNotReady(RuntimeError):
    """Raised when genuine service events do not cover the required window."""


@dataclass(frozen=True)
class DemoWorkloadConfig:
    gateway_url: str = "http://127.0.0.1:8000"
    baseline_seconds: float = DEFAULT_BASELINE_SECONDS
    challenge_seconds: float = DEFAULT_CHALLENGE_SECONDS
    recovery_seconds: float = DEFAULT_RECOVERY_SECONDS
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    bridge_wait_seconds: float = 8.0

    def __post_init__(self) -> None:
        if not self.gateway_url.startswith(("http://", "https://")):
            raise ValueError("demo workload gateway URL must be HTTP or HTTPS")
        durations = (
            self.baseline_seconds,
            self.challenge_seconds,
            self.recovery_seconds,
        )
        if min(*durations, self.requests_per_second, self.bridge_wait_seconds) <= 0:
            raise ValueError("demo workload durations and rates must be positive")
        if sum(durations) < MODEL_WINDOW_SECONDS + 2:
            raise ValueError("demo workload must provide more than one 60-second window")
        if self.max_concurrency <= 0:
            raise ValueError("demo workload concurrency must be positive")

    @classmethod
    def from_environment(cls) -> DemoWorkloadConfig:
        return cls(
            gateway_url=os.getenv(
                "FAULTWEAVE_INTERNAL_GATEWAY_URL", "http://127.0.0.1:8000"
            ).rstrip("/"),
            baseline_seconds=float(
                os.getenv("FAULTWEAVE_DEMO_BASELINE_SECONDS", DEFAULT_BASELINE_SECONDS)
            ),
            challenge_seconds=float(
                os.getenv("FAULTWEAVE_DEMO_CHALLENGE_SECONDS", DEFAULT_CHALLENGE_SECONDS)
            ),
            recovery_seconds=float(
                os.getenv("FAULTWEAVE_DEMO_RECOVERY_SECONDS", DEFAULT_RECOVERY_SECONDS)
            ),
            requests_per_second=float(
                os.getenv("FAULTWEAVE_DEMO_RPS", DEFAULT_REQUESTS_PER_SECOND)
            ),
            max_concurrency=int(
                os.getenv("FAULTWEAVE_DEMO_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY)
            ),
            bridge_wait_seconds=float(os.getenv("FAULTWEAVE_DEMO_BRIDGE_WAIT_SECONDS", "8")),
        )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class SustainedDemoWorkload:
    """Generate real research-path requests and retain their observed outcomes."""

    def __init__(
        self,
        buffer: LiveTelemetryBuffer,
        config: DemoWorkloadConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.buffer = buffer
        self.config = config or DemoWorkloadConfig.from_environment()
        self.transport = transport

    @property
    def profile(self) -> dict[str, Any]:
        return {
            "baseline_seconds": self.config.baseline_seconds,
            "challenge_seconds": self.config.challenge_seconds,
            "recovery_seconds": self.config.recovery_seconds,
            "total_seconds": round(
                self.config.baseline_seconds
                + self.config.challenge_seconds
                + self.config.recovery_seconds,
                3,
            ),
            "requests_per_second": self.config.requests_per_second,
            "traffic_path": "original_research_transaction_api",
            "financial_scope": "isolated_simulated_transactions",
        }

    def _payload(self, session_id: str, phase: str, sequence: int) -> dict[str, Any]:
        return {
            "username": os.getenv("DEMO_USERNAME", "demo"),
            "password": os.getenv("DEMO_PASSWORD", "faultweave-demo"),
            "account_number": os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001"),
            "amount_minor": 100 + sequence % 10_000,
            "currency": "INR",
            "recipient": f"model-window-{session_id[-8:]}-{phase}-{sequence:04d}",
        }

    async def _request(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        *,
        session_id: str,
        phase: str,
        sequence: int,
        offset_seconds: float,
        phase_clock: float,
    ) -> dict[str, Any]:
        delay = offset_seconds - (time.perf_counter() - phase_clock)
        if delay > 0:
            await asyncio.sleep(delay)
        request_id = f"{session_id}-model-{phase}-{sequence:04d}"
        trace_id = str(uuid5(NAMESPACE_URL, f"faultweave:{request_id}"))
        started_at = datetime.now(UTC)
        request_clock = time.perf_counter()
        status_code: int | None = None
        transport_error: str | None = None
        completed = False
        async with semaphore:
            try:
                response = await client.post(
                    "/api/v1/transactions",
                    headers={
                        "X-Run-ID": session_id,
                        "X-Request-ID": request_id,
                        "X-Trace-ID": trace_id,
                    },
                    json=self._payload(session_id, phase, sequence),
                )
                status_code = response.status_code
                if response.status_code == 200:
                    body = response.json()
                    completed = body.get("status") == "COMPLETED"
            except (httpx.RequestError, ValueError) as exc:
                transport_error = type(exc).__name__
        latency_ms = round((time.perf_counter() - request_clock) * 1000, 3)
        observation = LiveRequestObservation(
            request_id=request_id,
            trace_id=trace_id,
            started_at=started_at,
            latency_ms=latency_ms,
            status_code=status_code,
            expected_outcome=status_code == 200 and completed,
            transport_error=transport_error,
            scenario="valid",
        )
        self.buffer.ingest([], [observation])
        return {
            "status_code": status_code,
            "expected_outcome": observation.expected_outcome,
            "transport_error": transport_error,
            "latency_ms": latency_ms,
        }

    async def run_phase(
        self,
        session_id: str,
        phase: str,
        duration_seconds: float,
    ) -> dict[str, Any]:
        count = max(1, round(duration_seconds * self.config.requests_per_second))
        phase_started_at = datetime.now(UTC)
        phase_clock = time.perf_counter()
        semaphore = asyncio.Semaphore(self.config.max_concurrency)
        limits = httpx.Limits(
            max_connections=self.config.max_concurrency,
            max_keepalive_connections=max(5, self.config.max_concurrency // 2),
        )
        async with httpx.AsyncClient(
            base_url=self.config.gateway_url,
            timeout=15.0,
            limits=limits,
            transport=self.transport,
        ) as client:
            rows = await asyncio.gather(
                *(
                    self._request(
                        client,
                        semaphore,
                        session_id=session_id,
                        phase=phase,
                        sequence=index + 1,
                        offset_seconds=index / self.config.requests_per_second,
                        phase_clock=phase_clock,
                    )
                    for index in range(count)
                )
            )
        phase_ended_at = datetime.now(UTC)
        successful = sum(row["expected_outcome"] is True for row in rows)
        affected = len(rows) - successful
        return {
            "phase": phase,
            "started_at": _iso(phase_started_at),
            "ended_at": _iso(phase_ended_at),
            "duration_seconds": duration_seconds,
            "request_count": len(rows),
            "successful_count": successful,
            "affected_count": affected,
            "success_rate": round(successful / len(rows), 6),
            "server_error_count": sum(
                row["status_code"] is not None and int(row["status_code"]) >= 500 for row in rows
            ),
            "transport_error_count": sum(row["transport_error"] is not None for row in rows),
        }

    async def require_recent_events(self, phase_started_at: datetime) -> dict[str, Any]:
        """Prove that the external bridge is feeding current, genuine service events."""
        deadline = time.monotonic() + self.config.bridge_wait_seconds
        reason = "no current service event window was observed"
        while time.monotonic() < deadline:
            try:
                snapshot = self.buffer.snapshot(10, require_client_requests=True)
                ended = datetime.fromisoformat(
                    str(snapshot["window_ended_at"]).replace("Z", "+00:00")
                )
                if ended >= phase_started_at and snapshot["event_count"] > 0:
                    return {key: value for key, value in snapshot.items() if key != "features"}
                reason = "telemetry bridge has not ingested events from this demo run"
            except LiveWindowNotReady as exc:
                reason = str(exc)
            await asyncio.sleep(0.25)
        raise DemoTelemetryNotReady(
            f"{reason}; start scripts/live_telemetry_bridge.py before the blind challenge"
        )

    async def require_model_window(self) -> dict[str, Any]:
        """Wait for bridge flush and require a genuine 60-second feature window."""
        deadline = time.monotonic() + self.config.bridge_wait_seconds
        reason = "60-second live telemetry window is not ready"
        while time.monotonic() < deadline:
            try:
                snapshot = self.buffer.snapshot(MODEL_WINDOW_SECONDS, require_client_requests=True)
                if snapshot["event_count"] > 0 and snapshot["request_count"] > 0:
                    return {key: value for key, value in snapshot.items() if key != "features"}
                reason = "60-second window has no request or service-event evidence"
            except LiveWindowNotReady as exc:
                reason = str(exc)
            await asyncio.sleep(0.25)
        raise DemoTelemetryNotReady(reason)


    async def require_challenge_model_window(self, phase: dict[str, Any]) -> dict[str, Any]:
        """Fail closed unless the 60s v1 window lies inside challenge boundaries."""
        from .phase19b2_window import ChallengeWindowNotReady, challenge_only_snapshot
        try:
            return await challenge_only_snapshot(self.buffer, phase, self.config.bridge_wait_seconds)
        except ChallengeWindowNotReady as exc:
            raise DemoTelemetryNotReady(str(exc)) from exc


def get_demo_workload_runner() -> SustainedDemoWorkload:
    return SustainedDemoWorkload(get_live_telemetry_buffer())
