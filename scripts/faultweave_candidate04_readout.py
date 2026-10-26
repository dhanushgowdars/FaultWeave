"""Read-only local Command Center view of the isolated Candidate 04 watcher.

The browser cannot invoke inference. A prediction is shown only when a create-once
watcher receipt matches the isolated gateway's *current* independently observed
60-second feature vector and the frozen shadow service reports the expected model.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from scripts import faultweave_v2_candidate04_shadow_watch as watch
from scripts.faultweave_candidate04_acceptance_readout import acceptance_matrix, saved_acceptance_runs

FEATURES = "/api/v1/intelligence/live/features/60"
READY = "/api/v2/candidate04/ready"
SCHEMA = "/api/v2/candidate04/schema"
MAX_RECEIPT_LAG_SECONDS = 20


def _read_receipt(path: Path) -> tuple[dict, bytes]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("unsafe research receipt")
    raw = path.read_bytes()
    receipt = json.loads(raw)
    if not isinstance(receipt, dict):
        raise ValueError("invalid research receipt")
    return receipt, raw


def _safe_prediction(receipt: dict, checked: dict, *, exact: bool) -> None:
    if any((
        receipt.get("status") != "RESEARCH_ONLY_SHADOW_MONITOR",
        receipt.get("deployment_status") != "RESEARCH_ONLY_NOT_DEPLOYED",
        receipt.get("training_eligible") is not False,
        receipt.get("model_sha256", "").lower() != watch.MODEL_SHA256,
        receipt.get("window_seconds") != 60,
        receipt.get("request_count", 0) < 70,
        receipt.get("event_count", 0) < 70,
        not isinstance(receipt.get("feature_vector_sha256"), str),
        not re.fullmatch(r"[0-9a-f]{64}", receipt.get("feature_vector_sha256", "")),
        receipt.get("prediction") not in (
            "NORMAL", "UNKNOWN ABNORMAL PATTERN", "AUTHENTICATION_FAILURE_BURST",
            "CONNECTION_POOL_EXHAUSTION", "DATABASE_HIGH_LATENCY",
            "DATABASE_LOCK_CONTENTION", "DATABASE_UNAVAILABLE", "DOWNSTREAM_ERROR_BURST",
            "DOWNSTREAM_TIMEOUT", "HIGH_LOAD", "SERVICE_UNAVAILABLE"),
    )):
        raise ValueError("invalid research receipt")
    start = watch.parse_utc(receipt.get("window_started_at"))
    end = watch.parse_utc(receipt.get("window_ended_at"))
    current_end = watch.parse_utc(checked["ended_at"])
    if abs((end-start).total_seconds()-60) > .001 or not (0 <= (current_end-end).total_seconds() <= MAX_RECEIPT_LAG_SECONDS):
        raise ValueError("receipt is outside the recent V2 window")
    if exact and any((
        receipt.get("window_started_at") != checked["started_at"],
        receipt.get("window_ended_at") != checked["ended_at"],
        receipt.get("request_count") != checked["request_count"],
        receipt.get("event_count") != checked["event_count"],
        receipt.get("feature_vector_sha256") != watch.fingerprint(checked["features"]),
    )):
        raise ValueError("receipt does not match the current feature vector")


def _latest_recent_path(receipts: Path, checked: dict) -> Path | None:
    if not receipts.is_dir() or receipts.is_symlink():
        return None
    current_end = watch.parse_utc(checked["ended_at"])
    selected: tuple[datetime, Path] | None = None
    for path in receipts.glob("*.prediction.json"):
        # A bounded local research directory; malformed receipts fail closed.
        row, _ = _read_receipt(path)
        end = watch.parse_utc(row.get("window_ended_at"))
        if path.name != watch.receipt_path(end.isoformat()).name:
            raise ValueError("research receipt filename does not match its window")
        lag = (current_end-end).total_seconds()
        if 0 <= lag <= MAX_RECEIPT_LAG_SECONDS and (selected is None or end > selected[0]):
            selected = end, path
    return selected[1] if selected else None


def _saved_history(receipts: Path, now: datetime) -> list[dict]:
    """Show saved research evidence as history, never as a live health claim."""
    if not receipts.exists():
        return []
    if not receipts.is_dir() or receipts.is_symlink():
        raise ValueError("unsafe research receipt directory")
    paths = list(receipts.glob("*.prediction.json"))
    if len(paths) > 1000:
        raise ValueError("unbounded research receipt directory")
    rows = []
    for path in paths:
        receipt, raw = _read_receipt(path)
        start = watch.parse_utc(receipt.get("window_started_at"))
        end = watch.parse_utc(receipt.get("window_ended_at"))
        if (path.name != watch.receipt_path(end.isoformat()).name
                or abs((end - start).total_seconds() - 60) > .001
                or (end - now).total_seconds() > 5
                or receipt.get("status") != "RESEARCH_ONLY_SHADOW_MONITOR"
                or receipt.get("deployment_status") != "RESEARCH_ONLY_NOT_DEPLOYED"
                or receipt.get("training_eligible") is not False
                or receipt.get("model_sha256", "").lower() != watch.MODEL_SHA256
                or receipt.get("window_seconds") != 60
                or type(receipt.get("request_count")) is not int
                or receipt["request_count"] < 70
                or type(receipt.get("event_count")) is not int
                or receipt["event_count"] < 70
                or not isinstance(receipt.get("feature_vector_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", receipt["feature_vector_sha256"])
                or receipt.get("prediction") not in (
                    "NORMAL", "UNKNOWN ABNORMAL PATTERN", "AUTHENTICATION_FAILURE_BURST",
                    "CONNECTION_POOL_EXHAUSTION", "DATABASE_HIGH_LATENCY",
                    "DATABASE_LOCK_CONTENTION", "DATABASE_UNAVAILABLE", "DOWNSTREAM_ERROR_BURST",
                    "DOWNSTREAM_TIMEOUT", "HIGH_LOAD", "SERVICE_UNAVAILABLE")):
            raise ValueError("saved research receipt failed validation")
        rows.append({"prediction": receipt["prediction"],
                     "window_started_at": start.isoformat(),
                     "window_ended_at": end.isoformat(),
                     "request_count": receipt["request_count"],
                     "event_count": receipt["event_count"],
                     "feature_vector_sha256": receipt["feature_vector_sha256"],
                     "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                     "model_sha256": watch.MODEL_SHA256})
    return sorted(rows, key=lambda row: row["window_ended_at"], reverse=True)[:5]


def _historical_or_waiting(base: dict, receipts: Path, now: datetime,
                           fallback: str, detail: str) -> dict:
    history = _saved_history(receipts, now)
    if not history:
        return {**base, "status": fallback, "detail": detail}
    return {**base, "status": "HISTORICAL", "last_saved": history[0],
            "recent_receipts": history,
            "detail": "Saved frozen-model research receipts only; no current V2 prediction or health claim."}


def candidate_readout(
    get: Callable[[int, str], tuple[int, dict]],
    *,
    gateway_port: int = 28110,
    shadow_port: int = 28190,
    receipts: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Never return features, credentials, ground truth, or an unverified claim."""
    base = {"source": "isolated_v2_research", "deployment_status": "RESEARCH_ONLY_NOT_DEPLOYED"}
    receipts = receipts or watch.OUT
    now = now or datetime.now(UTC)
    try:
        base["acceptance_runs"] = saved_acceptance_runs(receipts=receipts)
        base["acceptance_matrix"] = acceptance_matrix(base["acceptance_runs"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        # A damaged optional lab report never turns a saved model receipt into
        # an incident claim or hides the independently verified V2 history.
        base["acceptance_runs"] = []
        base["acceptance_matrix"] = acceptance_matrix([])
        base["acceptance_detail"] = "Saved known-fault acceptance report is unavailable or unverified."
    try:
        from datasets.final_features import FEATURE_NAMES

        names = tuple(FEATURE_NAMES)
        if len(names) != 65 or len(set(names)) != 65:
            raise ValueError("feature schema changed")
        ready_status, ready = get(shadow_port, READY)
        schema_status, schema = get(shadow_port, SCHEMA)
        if (ready_status != 200 or schema_status != 200
                or ready.get("status") != "READY_FOR_RESEARCH_INFERENCE"
                or ready.get("deployment_status") != base["deployment_status"]
                or ready.get("model_sha256", "").lower() != watch.MODEL_SHA256
                or ready.get("feature_count") != 65
                or schema.get("feature_names") != list(names)
                or schema.get("model_sha256", "").lower() != watch.MODEL_SHA256):
            raise ValueError("frozen shadow identity or schema mismatch")
        status, snapshot = get(gateway_port, FEATURES)
        if status == 409:
            return _historical_or_waiting(
                base, receipts, now, "AWAITING_TELEMETRY",
                "No complete independently observed V2 window is available.")
        if status != 200:
            raise ValueError("isolated gateway unavailable")
        checked = watch.snapshot_gate(snapshot, names, now=now)
        if checked["kind"] == "SKIP_STALE":
            return _historical_or_waiting(
                base, receipts, now, "STALE", "The last V2 window is older than 20 seconds.")
        expected = receipts / watch.receipt_path(checked["ended_at"]).name
        exact = expected.exists() or expected.is_symlink()
        path = expected if exact else _latest_recent_path(receipts, checked)
        if path is None:
            return {**base, "status": "AWAITING_PREDICTION",
                    "detail": "Fresh V2 telemetry exists; the shadow watcher has not recorded this window."}
        receipt, raw = _read_receipt(path)
        try:
            _safe_prediction(receipt, checked, exact=exact)
        except (ValueError, watch.WatchError):
            if exact:
                # A same-window receipt that disagrees with observed features
                # must never be relabelled as merely historical.
                return {**base, "status": "UNAVAILABLE",
                        "detail": "The saved V2 receipt conflicts with the current feature window."}
            raise
        return {**base, "status": "PREDICTED" if exact else "RECENT_PREDICTION",
                "prediction": receipt["prediction"],
                "window_ended_at": receipt["window_ended_at"],
                "request_count": receipt["request_count"], "event_count": receipt["event_count"],
                "model_sha256": watch.MODEL_SHA256,
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                "recent_receipts": _saved_history(receipts, now),
                "detail": ("Research receipt matches the current isolated V2 feature window."
                           if exact else "Recent research receipt; current V2 window has advanced since inference.")}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, watch.WatchError):
        # The currently running shadow may be offline. Independently validated
        # create-once receipts still exist as historical research evidence.
        try:
            return _historical_or_waiting(
                base, receipts, now, "UNAVAILABLE",
                "Isolated V2 model, telemetry, or matching receipt could not be verified.")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, watch.WatchError):
            return {**base, "status": "UNAVAILABLE",
                    "detail": "Isolated V2 model, telemetry, or matching receipt could not be verified."}
