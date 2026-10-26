"""Read-only, fail-closed presentation of the existing gateway's telemetry.

This route does not call inference. Existing gateway incidents come from the
earlier frozen model; Candidate 04 remains an isolated research shadow service.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .intelligence_live import LiveWindowNotReady

MAX_AGE_SECONDS = 20


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("telemetry timestamp must have a timezone")
    return parsed.astimezone(UTC)


def _incident_summary(row: dict[str, Any]) -> dict[str, Any]:
    classification = row.get("classification") or {}
    localization = row.get("localization") or {}
    return {
        "incident_id": row.get("incident_id"),
        "state": row.get("state"),
        "detected_at": row.get("detected_at"),
        "updated_at": row.get("updated_at"),
        "fault_family": classification.get("label"),
        "probable_origin": localization.get("probable_origin"),
        "localization_method": localization.get("method"),
    }


def command_center_snapshot(buffer: Any, manager: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    observation = buffer.observation_status()
    last_event_at = observation["last_event_at"]
    telemetry: dict[str, Any] = {
        "status": "UNOBSERVED",
        "last_event_at": last_event_at,
        "age_seconds": None,
        "window_seconds": 60,
        "event_count": None,
        "request_count": None,
        "coverage_seconds": None,
        "detail": "No telemetry has been observed by this gateway process.",
    }
    if last_event_at:
        age = (now - _timestamp(last_event_at)).total_seconds()
        telemetry["age_seconds"] = round(age, 3)
        if age < -5:
            telemetry.update(status="CLOCK_SKEW", detail="Event timestamp is ahead of gateway time.")
        elif age > MAX_AGE_SECONDS:
            telemetry.update(status="STALE", detail="Last observed event is older than 20 seconds.")
        else:
            try:
                window = buffer.snapshot(60, require_client_requests=True)
            except LiveWindowNotReady:
                telemetry.update(
                    status="INSUFFICIENT",
                    detail="A complete 60-second window with independent client requests is required.",
                )
            else:
                if window["request_count"] < 1 or window["request_observation_source"] != "client_observed":
                    telemetry.update(
                        status="INSUFFICIENT",
                        detail="No independent client requests fall inside the complete window.",
                    )
                else:
                    telemetry.update(
                        status="READY",
                        detail="Fresh complete telemetry window. This view performs no inference.",
                        event_count=window["event_count"],
                        request_count=window["request_count"],
                        coverage_seconds=window["coverage_seconds"],
                    )

    rows = manager.incidents()
    current = manager.current_incident()
    incident_fresh = current is None
    if current is not None:
        try:
            incident_age = (now - _timestamp(current["updated_at"])).total_seconds()
            incident_fresh = -5 <= incident_age <= MAX_AGE_SECONDS
        except (KeyError, TypeError, ValueError):
            incident_fresh = False
    return {
        "schema_version": 1,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "telemetry": telemetry,
        "incident_feed": {
            "source_model": "earlier_gateway_frozen_model",
            "status": "CURRENT" if telemetry["status"] == "READY" and incident_fresh else "UNVERIFIED",
            "count": len(rows),
            "current": _incident_summary(current) if current else None,
            "recent": [_incident_summary(row) for row in rows[:5]],
            "durability": "in_memory_gateway_process",
        },
        "candidate04": {
            "status": "NOT_CONNECTED",
            "reason": "Frozen Candidate 04 is a separate research shadow API.",
        },
    }
