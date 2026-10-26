"""Engineer-only, read-only projections of existing transfer case evidence.

The local Site preview calls the original gateway. This module neither runs a
scenario nor requests new inference or an AI provider response. Earlier-model
assistant snapshots are displayed only if already saved with a demo session.
"""

from __future__ import annotations

import re
from typing import Callable

Get = Callable[[int, str, str], tuple[int, dict]]
UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")


class CaseRouteError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _engineer(get: Get, port: int, authorization: str) -> None:
    status, _ = get(port, "/api/v1/demo/contract", authorization)
    if status != 200:
        raise CaseRouteError(403 if status == 403 else 401, "Engineer access required")


def _read(get: Get, port: int, path: str, authorization: str) -> dict:
    status, value = get(port, path, authorization)
    if status == 404:
        raise CaseRouteError(404, "Case evidence was not found")
    if status in (401, 403):
        raise CaseRouteError(status, "Engineer session was rejected")
    if status != 200 or not isinstance(value, dict):
        raise CaseRouteError(503, "Case evidence service is unavailable")
    return value


def _summary(row: dict) -> dict:
    if not isinstance(row, dict) or not UUID.fullmatch(str(row.get("id", ""))):
        raise ValueError("Unexpected transfer summary")
    return {key: row.get(key) for key in (
        "id", "state", "funds_status", "amount_minor", "currency", "created_at",
        "latest_event_at", "requires_review", "failure_count", "review_count", "recovery_count",
    )}


def list_cases(get: Get, port: int, authorization: str) -> dict:
    _engineer(get, port, authorization)
    value = _read(get, port, "/api/v1/transfers?limit=20&offset=0", authorization)
    if value.get("schema_version") != "1.0" or value.get("scope") != "engineer":
        raise ValueError("Engineer case contract mismatch")
    rows = value.get("items")
    page = value.get("page")
    if not isinstance(rows, list) or len(rows) > 20 or not isinstance(page, dict):
        raise ValueError("Invalid case listing")
    return {"schema_version": 1, "source": "persisted_transfer_evidence",
            "items": [_summary(row) for row in rows],
            "total": page.get("total"), "has_more": page.get("has_more")}


def _saved_assistant(get: Get, port: int, authorization: str,
                     transfer_id: str, event_ids: set[str]) -> dict | None:
    # A failure to read optional historic explanations must not hide real evidence.
    try:
        value = _read(get, port, "/api/v1/demo/sessions?limit=100", authorization)
        rows = value.get("items")
        if not isinstance(rows, list):
            return None
        for session in rows:
            if not isinstance(session, dict) or session.get("transfer_id") != transfer_id:
                continue
            if session.get("analysis_status") != "READY":
                return None
            assistant = (session.get("snapshots") or {}).get("assistant") or {}
            if assistant.get("transfer_id") != transfer_id or assistant.get("schema_version") != "1.0":
                return None
            analysis = assistant.get("analysis") or {}
            provenance = assistant.get("provenance") or {}
            if provenance.get("ground_truth_used") is not False:
                return None
            statements = {}
            for key in ("first_failure", "funds_state", "model_assessment", "probable_cause"):
                statement = analysis.get(key) or {}
                ids = statement.get("evidence_event_ids")
                if not isinstance(ids, list) or not set(ids).issubset(event_ids):
                    return None
                statements[key] = {"kind": statement.get("kind"), "text": statement.get("text"),
                                   "evidence_event_ids": ids}
            return {"status": assistant.get("status"),
                    "headline": analysis.get("headline"),
                    "executive_summary": analysis.get("executive_summary"),
                    "statements": statements,
                    "limitations": analysis.get("limitations") or [],
                    "mode": provenance.get("mode"),
                    "source_model": "earlier_gateway_frozen_model",
                    "note": "Saved explanation; no new AI request was made."}
    except (CaseRouteError, OSError, TypeError, ValueError, AttributeError):
        pass
    return None


def case_detail(get: Get, port: int, authorization: str, transfer_id: str) -> dict:
    if not UUID.fullmatch(transfer_id):
        raise CaseRouteError(404, "Case evidence was not found")
    _engineer(get, port, authorization)
    value = _read(get, port, f"/api/v1/transfers/{transfer_id}/evidence", authorization)
    if value.get("schema_version") != "1.0" or value.get("transfer_id") != transfer_id:
        raise ValueError("Case evidence identity mismatch")
    raw_events = value.get("timeline")
    if not isinstance(raw_events, list) or len(raw_events) > 1000:
        raise ValueError("Invalid case evidence timeline")
    events = []
    for event in raw_events:
        if not isinstance(event, dict) or not event.get("event_id") or not event.get("timestamp"):
            raise ValueError("Invalid case event")
        events.append({key: event.get(key) for key in (
            "event_id", "timestamp", "service", "step", "status", "detail",
            "request_id", "trace_id", "attempt",
        )})
    events.sort(key=lambda item: (str(item["timestamp"]), str(item["event_id"])))
    failed = [event for event in events if event["status"] == "FAILED"]
    investigation = value.get("investigation") or {}
    if not isinstance(investigation, dict):
        raise ValueError("Invalid saved investigation")
    cited = investigation.get("evidence_event_ids") or []
    if (not isinstance(cited, list) or len(cited) > 1000
            or any(not isinstance(event_id, str) for event_id in cited)
            or not set(cited).issubset({str(event["event_id"]) for event in failed})):
        raise ValueError("Saved investigation cites events outside the failed timeline")
    first = failed[0] if failed else None
    first_failure = ({key: first.get(key) for key in
                      ("event_id", "timestamp", "service", "step", "status", "detail")}
                     if first else None)
    # The first failed workflow event is observable. A dependency's root cause
    # cannot be proved solely from its service label or a model prediction.
    localization = {
        "status": "OBSERVED_FAILURE_ROOT_CAUSE_UNCONFIRMED" if first else "NO_FAILED_EVENT_RECORDED",
        "first_failure": first_failure,
        "cited_failure_event_ids": cited,
        "reported_failure_service": investigation.get("observed_failure_service")
            if first and investigation.get("observed_failure_service") in
            {event["service"] for event in failed} else None,
        "root_cause_confirmed": False,
    }
    source = value.get("evidence_sources") or {}
    if not isinstance(source, dict) or source.get("workflow") != "transactions.transfer_workflows":
        raise ValueError("Case evidence lacks persisted workflow provenance")
    transfer = value.get("transfer") or {}
    journal = value.get("journal") or {}
    entries = journal.get("entries") or []
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError("Invalid case journal")
    safe_entries = [{"direction": row.get("direction"), "amount_minor": row.get("amount_minor"),
                     "currency": row.get("currency"), "created_at": row.get("created_at")}
                    for row in entries if isinstance(row, dict)]
    assistant = _saved_assistant(get, port, authorization, transfer_id,
                                 {str(row["event_id"]) for row in events})
    return {"schema_version": 1, "source": "persisted_transfer_evidence",
            "source_model": "earlier_gateway_frozen_model", "transfer_id": transfer_id,
            "state": value.get("state"), "funds_status": value.get("funds_status"),
            "requires_review": value.get("requires_review"), "created_at": value.get("created_at"),
            "amount_minor": transfer.get("amount_minor"), "currency": transfer.get("currency"),
            "timeline": events, "journal_entries": safe_entries,
            "localization": localization,
            "evidence_sources": {key: source.get(key) for key in ("workflow", "payment", "journal")},
            "case_history_counts": {key: len((value.get("case_history") or {}).get(key) or [])
                                    for key in ("failures", "reviews", "recoveries")},
            "saved_assistant": assistant,
            "candidate04": "Separate research shadow; no per-transfer Candidate 04 claim."}
