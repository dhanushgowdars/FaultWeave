"""Phase 19C.2B: strict, read-only, exact-60-second v2 research window contract.

This module does not collect data, inspect protected fault labels, alter model v1,
or train a model. It provides fail-closed phase/fault boundaries and builds a
65-feature vector using the existing training feature formula implementation.

Input records must be gathered from genuine client requests / structured logs.
The model vector contains ONLY observable features; labels remain external.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable


class V2WindowContractError(ValueError):
    """The proposed sample is not a valid v2 research observation."""


@dataclass(frozen=True)
class ExactWindow:
    started_at: datetime
    ended_at: datetime
    window_seconds: int = 60

    def __post_init__(self) -> None:
        if self.window_seconds != 60:
            raise V2WindowContractError("v2 research window must be exactly 60 seconds")
        start = _utc(self.started_at)
        end = _utc(self.ended_at)
        if end - start != timedelta(seconds=60):
            raise V2WindowContractError("window physical duration must be exactly 60.000 seconds")
        object.__setattr__(self, "started_at", start)
        object.__setattr__(self, "ended_at", end)


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise V2WindowContractError("invalid ISO timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise V2WindowContractError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def select_exact_window(
    *,
    phase_started_at: datetime | str,
    phase_ended_at: datetime | str,
    telemetry_watermark: datetime | str,
    fault_activated_at: datetime | str | None = None,
    fault_expires_at: datetime | str | None = None,
    fault_cleared_at: datetime | str | None = None,
    fault_required: bool,
    lead_guard_seconds: float = 2.0,
    tail_guard_seconds: float = 2.0,
) -> ExactWindow:
    """Choose a verified (start,end] window, strictly inside the declared phase.

    In a fault window, both the arm and cleanup timestamps are mandatory proof.
    If phase length, fault lifetime, or telemetry watermark is insufficient,
    FAIL rather than padding, mixing, or shortening the window.
    """
    if not all(math.isfinite(v) and v >= 0 for v in (lead_guard_seconds, tail_guard_seconds)):
        raise V2WindowContractError("guards must be nonnegative finite seconds")
    begin = _utc(phase_started_at)
    phase_end = _utc(phase_ended_at)
    watermark = _utc(telemetry_watermark)
    if phase_end <= begin:
        raise V2WindowContractError("invalid phase boundaries")
    if fault_required:
        if any(v is None for v in (fault_activated_at, fault_expires_at, fault_cleared_at)):
            raise V2WindowContractError("fault window requires verified arm, expiry and cleanup times")
        arm = _utc(fault_activated_at)  # type: ignore[arg-type]
        expiry = _utc(fault_expires_at)  # type: ignore[arg-type]
        cleared = _utc(fault_cleared_at)  # type: ignore[arg-type]
        if not arm < min(expiry, cleared):
            raise V2WindowContractError("fault was not active for a valid positive interval")
        begin = max(begin, arm)
        phase_end = min(phase_end, expiry, cleared)
    elif any(v is not None for v in (fault_activated_at, fault_expires_at, fault_cleared_at)):
        raise V2WindowContractError("normal window must not carry fault activation metadata")
    latest_end = min(phase_end - timedelta(seconds=tail_guard_seconds), watermark)
    earliest_start = begin + timedelta(seconds=lead_guard_seconds)
    candidate_start = latest_end - timedelta(seconds=60)
    if candidate_start < earliest_start:
        raise V2WindowContractError(
            "insufficient 60-second continuous phase/fault/telemetry coverage; "
            "do not pad or borrow baseline/recovery evidence"
        )
    return ExactWindow(started_at=candidate_start, ended_at=latest_end)


def _runtime_eligible(event: dict[str, Any]) -> bool:
    """Mirror existing live eligibility; test guard detects future divergence."""
    services = {"gateway", "authentication", "transaction", "payment", "account", "ledger"}
    if event.get("service") not in services:
        return False
    kind = str(event.get("event_type") or "")
    path = str(event.get("path") or "")
    if kind == "health_check_completed" or path.startswith("/health/"):
        return False
    if kind.startswith("intelligence_") or path.startswith("/api/v1/intelligence/"):
        return False
    return True


def build_observable_features(
    *,
    run_id: str,
    window: ExactWindow,
    requests: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
    require_boundary_evidence: bool = True,
    edge_coverage_seconds: float = 5.0,
) -> dict[str, float]:
    """Build v2 candidate features ONLY from run-scoped observable records.

    Same timestamps as live: start < observation_timestamp <= end. Requires
    explicit run_id on client observations (a new-v2 collection contract);
    old v1 requests must NOT silently acquire a fake run ID.
    This builds an input vector, NOT an accepted labeled training example.
    """
    if not run_id or not isinstance(run_id, str):
        raise V2WindowContractError("explicit nonempty run_id required")
    if not math.isfinite(edge_coverage_seconds) or not 0 <= edge_coverage_seconds < 30:
        raise V2WindowContractError("invalid edge coverage tolerance")
    selected_requests: list[dict[str, Any]] = []
    selected_events: list[dict[str, Any]] = []
    ids: set[str] = set()
    seen_events: set[str] = set()
    for row in requests:
        if not isinstance(row, dict):
            raise V2WindowContractError("malformed request record")
        if "run_id" not in row or "started_at" not in row:
            raise V2WindowContractError("v2 client request missing run_id/started_at")
        timestamp = _utc(row["started_at"])
        if row["run_id"] != run_id or not window.started_at < timestamp <= window.ended_at:
            continue
        rid = row.get("request_id")
        if not isinstance(rid, str) or not rid or rid in ids:
            raise V2WindowContractError("duplicate or missing client request ID")
        ids.add(rid)
        selected_requests.append(row)
    for row in events:
        if not isinstance(row, dict):
            raise V2WindowContractError("malformed event record")
        if "run_id" not in row or "timestamp" not in row:
            raise V2WindowContractError("v2 service event missing run_id/timestamp")
        timestamp = _utc(row["timestamp"])
        if row["run_id"] != run_id or not window.started_at < timestamp <= window.ended_at:
            continue
        if not _runtime_eligible(row):
            continue
        fingerprint = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        if fingerprint in seen_events:
            continue  # match live buffer's exact-duplicate suppression
        seen_events.add(fingerprint)
        selected_events.append(row)
    if not selected_requests or not selected_events:
        raise V2WindowContractError("window lacks real client requests or eligible service events")
    if require_boundary_evidence:
        tolerance = timedelta(seconds=edge_coverage_seconds)
        for kind, rows, key in (
            ("request", selected_requests, "started_at"),
            ("event", selected_events, "timestamp"),
        ):
            first, last = min(_utc(x[key]) for x in rows), max(_utc(x[key]) for x in rows)
            if first > window.started_at + tolerance or last < window.ended_at - tolerance:
                raise V2WindowContractError(f"{kind} observations do not span the full window")
    # Use existing training formulas, with the exact same fixed 60s denominator.
    # The test suite asserts numerical agreement with the live implementation.
    from datasets.final_features import FEATURE_NAMES, _event_features, _request_features

    vector = _request_features(selected_requests, 60.0)
    vector.update(_event_features(selected_events, 60.0))
    if tuple(vector) != FEATURE_NAMES or len(vector) != 65:
        raise V2WindowContractError("unexpected 65-feature schema/order mismatch")
    if any(not math.isfinite(float(value)) for value in vector.values()):
        raise V2WindowContractError("nonfinite feature computed")
    return vector
