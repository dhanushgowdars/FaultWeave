"""Engineer readout for saved isolated fault acceptance, separate from live incidents.

Read-only. Every displayed model result must match a frozen watcher receipt by
SHA256; reported fault labels are audit context and never model inputs.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts import faultweave_v2_candidate04_shadow_watch as watch

RUN_ID = re.compile(r"v2observer-[0-9a-f]{16}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
PREDICTIONS = frozenset((
    "NORMAL", "UNKNOWN ABNORMAL PATTERN", "AUTHENTICATION_FAILURE_BURST",
    "CONNECTION_POOL_EXHAUSTION", "DATABASE_HIGH_LATENCY", "DATABASE_LOCK_CONTENTION",
    "DATABASE_UNAVAILABLE", "DOWNSTREAM_ERROR_BURST", "DOWNSTREAM_TIMEOUT",
    "HIGH_LOAD", "SERVICE_UNAVAILABLE",
))
KNOWN_FAMILIES = (
    "AUTHENTICATION_FAILURE_BURST", "CONNECTION_POOL_EXHAUSTION",
    "DATABASE_HIGH_LATENCY", "DATABASE_LOCK_CONTENTION", "DATABASE_UNAVAILABLE",
    "DOWNSTREAM_ERROR_BURST", "DOWNSTREAM_TIMEOUT", "HIGH_LOAD",
    "SERVICE_UNAVAILABLE",
)
DEFAULT_REPORTS = watch.ROOT / ".runtime" / "v2-isolated-fault-acceptance"


def _bounded_json(path: Path) -> tuple[dict, bytes]:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 131072:
        raise ValueError("unsafe acceptance artifact")
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid acceptance artifact")
    return value, raw


def saved_acceptance_runs(reports: Path = DEFAULT_REPORTS,
                          receipts: Path | None = None) -> list[dict]:
    receipts = receipts or watch.OUT
    if not reports.exists():
        return []
    if reports.is_symlink() or not reports.is_dir() or receipts.is_symlink():
        raise ValueError("unsafe acceptance directory")
    paths = list(reports.glob("*.result.json"))
    receipt_paths = list(receipts.glob("*.prediction.json")) if receipts.is_dir() else []
    if len(paths) > 1000 or len(receipt_paths) > 1000:
        raise ValueError("unbounded acceptance history")
    by_hash = {}
    for path in receipt_paths:
        value, raw = _bounded_json(path)
        by_hash[hashlib.sha256(raw).hexdigest()] = value
    result = []
    for path in paths:
        report, raw = _bounded_json(path)
        run_id = report.get("run_id")
        if (not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id)
                or path.name != run_id + ".result.json"
                or report.get("status") not in ("PASS", "MISMATCH")
                or report.get("expected_family") != "SERVICE_UNAVAILABLE"
                or report.get("fault_target") != "payment"
                or report.get("frozen_model_sha256") != watch.MODEL_SHA256
                or report.get("training_eligible") is not False
                or report.get("threshold_tuning_eligible") is not False
                or report.get("ground_truth_used_for_inference") is not False
                or report.get("original_gateway_written") is not False
                or report.get("fault_cleared") is not True
                or report.get("total_client_requests") != 113
                or report.get("recovery_completed_requests") != 8):
            raise ValueError("acceptance provenance differs")
        failures = report.get("observed_failed_client_requests")
        service_count = report.get("failed_service_event_count")
        if (type(failures) is not int or not 0 <= failures <= 113
                or type(service_count) is not int or not 0 <= service_count <= 100000):
            raise ValueError("invalid observed failure counts")
        rows = report.get("eligible_frozen_receipts")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 10:
            raise ValueError("missing bounded frozen receipts")
        predictions = []
        hashes = []
        for item in rows:
            if not isinstance(item, dict) or not isinstance(item.get("receipt_sha256"), str):
                raise ValueError("invalid receipt reference")
            digest = item["receipt_sha256"]
            original = by_hash.get(digest) if SHA.fullmatch(digest) else None
            if (not isinstance(original, dict) or original.get("model_sha256") != watch.MODEL_SHA256
                    or original.get("status") != "RESEARCH_ONLY_SHADOW_MONITOR"
                    or original.get("deployment_status") != "RESEARCH_ONLY_NOT_DEPLOYED"
                    or original.get("training_eligible") is not False
                    or original.get("prediction") not in PREDICTIONS
                    or any(original.get(key) != item.get(key) for key in
                           ("window_started_at", "window_ended_at", "request_count",
                            "event_count", "prediction"))):
                raise ValueError("acceptance receipt does not match frozen evidence")
            predictions.append(original["prediction"])
            hashes.append(digest)
        effect = failures >= 10
        predicted = "SERVICE_UNAVAILABLE" in predictions
        if (report.get("observed_effect") is not effect
                or report.get("family_predicted") is not predicted
                or report.get("service_failure_evidence_observed") is not (service_count > 0)
                or (report["status"] == "PASS") is not (effect and predicted and service_count > 0)):
            raise ValueError("acceptance verdict conflicts with saved observations")
        evidence = report.get("first_failure_evidence")
        if not isinstance(evidence, list) or len(evidence) > 12:
            raise ValueError("invalid service evidence list")
        for event in evidence:
            if (not isinstance(event, dict) or not isinstance(event.get("event_sha256"), str)
                    or not SHA.fullmatch(event["event_sha256"])):
                raise ValueError("invalid service evidence fingerprint")
        result.append({
            "run_id": run_id, "status": report["status"],
            "expected_family": report["expected_family"],
            "fault_target": report["fault_target"],
            "observed_failed_client_requests": failures,
            "failed_service_event_count": service_count,
            "model_predictions": predictions,
            "receipt_sha256": hashes,
            "recovery_completed_requests": 8,
            "first_failure_evidence": [{key: event.get(key) for key in
                ("timestamp", "service", "downstream_service", "event_type", "event_sha256")}
                for event in evidence[:3]],
            "report_sha256": hashlib.sha256(raw).hexdigest(),
            "created_at": report.get("created_at"),
            "scope": "isolated_known_fault_research_only",
            "root_cause_confirmed": False,
        })
    return sorted(result, key=lambda item: str(item["created_at"]), reverse=True)[:5]


def acceptance_matrix(runs: list[dict]) -> list[dict]:
    """Return an explicit nine-family coverage matrix without inventing runs."""
    latest = {}
    for row in runs:
        family = row.get("expected_family")
        if family in KNOWN_FAMILIES and family not in latest:
            latest[family] = row
    matrix = []
    for family in KNOWN_FAMILIES:
        row = latest.get(family)
        if row is None:
            matrix.append({"family": family, "status": "NOT_RUN",
                           "scope": "isolated_live_acceptance", "run_id": None})
            continue
        matrix.append({
            "family": family, "status": row["status"],
            "scope": "isolated_live_acceptance", "run_id": row["run_id"],
            "fault_target": row["fault_target"],
            "model_predictions": row["model_predictions"],
            "report_sha256": row["report_sha256"],
        })
    return matrix
