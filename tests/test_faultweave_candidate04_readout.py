"""Offline checks: only a receipt for the current V2 feature window can be shown."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from datasets.final_features import FEATURE_NAMES
from scripts.faultweave_candidate04_readout import candidate_readout, FEATURES, READY, SCHEMA
from scripts import faultweave_v2_candidate04_shadow_watch as watch

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
NAMES = tuple(FEATURE_NAMES)


def snapshot(*, age=5):
    end = NOW - timedelta(seconds=age)
    return {
        "window_seconds": 60, "request_observation_source": "client_observed",
        "window_started_at": (end - timedelta(seconds=60)).isoformat(),
        "window_ended_at": end.isoformat(), "coverage_seconds": 62,
        "request_count": 90, "event_count": 2250,
        "features": {name: float(i) for i, name in enumerate(NAMES)},
    }


def gateway(row):
    calls = []
    def get(port, route):
        calls.append((port, route))
        if route == READY:
            return 200, {"status": "READY_FOR_RESEARCH_INFERENCE",
                         "deployment_status": "RESEARCH_ONLY_NOT_DEPLOYED",
                         "model_sha256": watch.MODEL_SHA256, "feature_count": 65}
        if route == SCHEMA:
            return 200, {"feature_names": list(NAMES), "model_sha256": watch.MODEL_SHA256}
        if route == FEATURES:
            return (409, {"detail": "no live telemetry has been observed"}) if row is None else (200, row)
        raise AssertionError("unexpected route")
    return get, calls


def test_missing_or_stale_telemetry_never_displays_a_prediction(tmp_path):
    get, calls = gateway(None)
    result = candidate_readout(get, receipts=tmp_path, now=NOW)
    assert result["status"] == "AWAITING_TELEMETRY"
    assert len(result["acceptance_matrix"]) == 9
    assert all(row["status"] == "NOT_RUN" for row in result["acceptance_matrix"])
    assert "prediction" not in result
    assert all(route != "/api/v2/candidate04/infer" for _, route in calls)
    old, _ = gateway(snapshot(age=40))
    assert candidate_readout(old, receipts=tmp_path, now=NOW)["status"] == "STALE"


def test_fresh_window_requires_matching_create_once_receipt(tmp_path):
    row = snapshot()
    get, calls = gateway(row)
    waiting = candidate_readout(get, receipts=tmp_path, now=NOW)
    assert waiting["status"] == "AWAITING_PREDICTION"
    checked = watch.snapshot_gate(row, NAMES, now=NOW)
    path = tmp_path / watch.receipt_path(checked["ended_at"]).name
    receipt = {
        "status": "RESEARCH_ONLY_SHADOW_MONITOR",
        "deployment_status": "RESEARCH_ONLY_NOT_DEPLOYED", "training_eligible": False,
        "model_sha256": watch.MODEL_SHA256, "window_seconds": 60,
        "window_started_at": checked["started_at"], "window_ended_at": checked["ended_at"],
        "request_count": checked["request_count"], "event_count": checked["event_count"],
        "feature_vector_sha256": watch.fingerprint(checked["features"]),
        "prediction": "NORMAL",
    }
    path.write_text(json.dumps(receipt), encoding="utf-8")
    result = candidate_readout(get, receipts=tmp_path, now=NOW)
    assert result["status"] == "PREDICTED"
    assert result["prediction"] == "NORMAL"
    assert len(result["receipt_sha256"]) == 64
    assert "features" not in result
    assert set(calls) == {(28190, READY), (28190, SCHEMA), (28110, FEATURES)}
    receipt["feature_vector_sha256"] = "a" * 64
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert candidate_readout(get, receipts=tmp_path, now=NOW)["status"] == "UNAVAILABLE"


def test_schema_or_client_provenance_mismatch_fails_closed(tmp_path):
    row = snapshot()
    row["request_observation_source"] = "gateway_derived"
    get, _ = gateway(row)
    assert candidate_readout(get, receipts=tmp_path, now=NOW)["status"] == "UNAVAILABLE"
    get, _ = gateway(snapshot())
    def bad_schema(port, route):
        status, payload = get(port, route)
        if route == SCHEMA:
            payload["feature_names"] = payload["feature_names"][:-1]
        return status, payload
    assert candidate_readout(bad_schema, receipts=tmp_path, now=NOW)["status"] == "UNAVAILABLE"


def test_recent_receipt_is_labeled_historical_when_gateway_window_advances(tmp_path):
    previous = snapshot(age=12)
    checked = watch.snapshot_gate(previous, NAMES, now=NOW)
    path = tmp_path / watch.receipt_path(checked["ended_at"]).name
    path.write_text(json.dumps({
        "status": "RESEARCH_ONLY_SHADOW_MONITOR",
        "deployment_status": "RESEARCH_ONLY_NOT_DEPLOYED", "training_eligible": False,
        "model_sha256": watch.MODEL_SHA256, "window_seconds": 60,
        "window_started_at": checked["started_at"], "window_ended_at": checked["ended_at"],
        "request_count": 90, "event_count": 2250,
        "feature_vector_sha256": watch.fingerprint(checked["features"]),
        "prediction": "NORMAL",
    }), encoding="utf-8")
    current, _ = gateway(snapshot(age=4))
    result = candidate_readout(current, receipts=tmp_path, now=NOW)
    assert result["status"] == "RECENT_PREDICTION"
    assert result["window_ended_at"] == checked["ended_at"]
    later, _ = gateway(snapshot(age=-20))
    result = candidate_readout(later, receipts=tmp_path, now=NOW)
    assert result["status"] == "HISTORICAL"
    assert "prediction" not in result


def test_watcher_skips_overlapping_window_before_shadow_inference(tmp_path):
    row = snapshot(age=5)
    earlier = watch.snapshot_gate(snapshot(age=17), NAMES, now=NOW)
    path = tmp_path / watch.receipt_path(earlier["ended_at"]).name
    path.write_text(json.dumps({"model_sha256": watch.MODEL_SHA256,
                                "window_ended_at": earlier["ended_at"]}), encoding="utf-8")
    previous_out, previous_api = watch.OUT, watch.api
    watch.OUT = tmp_path
    def read_only_api(url, payload=None):
        if payload is not None:
            raise AssertionError("overlapping window must never invoke the model")
        return 200, row
    watch.api = read_only_api
    try:
        assert watch.one_poll(NAMES, infer=True, now=NOW) == "OVERLAP"
    finally:
        watch.OUT, watch.api = previous_out, previous_api


def test_saved_results_remain_historical_after_window_stales(tmp_path):
    previous = snapshot(age=80)
    checked = watch.snapshot_gate(previous, NAMES, now=NOW - timedelta(seconds=75))
    path = tmp_path / watch.receipt_path(checked["ended_at"]).name
    path.write_text(json.dumps({
        "status": "RESEARCH_ONLY_SHADOW_MONITOR",
        "deployment_status": "RESEARCH_ONLY_NOT_DEPLOYED", "training_eligible": False,
        "model_sha256": watch.MODEL_SHA256, "window_seconds": 60,
        "window_started_at": checked["started_at"], "window_ended_at": checked["ended_at"],
        "request_count": 90, "event_count": 2250,
        "feature_vector_sha256": watch.fingerprint(checked["features"]),
        "prediction": "NORMAL",
    }), encoding="utf-8")
    stale, _ = gateway(previous)
    result = candidate_readout(stale, receipts=tmp_path, now=NOW)
    assert result["status"] == "HISTORICAL"
    assert result["last_saved"]["prediction"] == "NORMAL"
    assert result["last_saved"]["receipt_sha256"]
    assert result["last_saved"]["feature_vector_sha256"] == watch.fingerprint(checked["features"])
    assert result["last_saved"]["window_started_at"] == checked["started_at"]
    assert "current V2 prediction" in result["detail"]
    no_window, _ = gateway(None)
    assert candidate_readout(no_window, receipts=tmp_path, now=NOW)["status"] == "HISTORICAL"
    # A changed receipt cannot be silently presented as the same research evidence.
    content = json.loads(path.read_text())
    content["model_sha256"] = "0" * 64
    path.write_text(json.dumps(content), encoding="utf-8")
    assert candidate_readout(stale, receipts=tmp_path, now=NOW)["status"] == "UNAVAILABLE"


def test_shadow_offline_retains_only_valid_historical_receipts(tmp_path):
    previous = snapshot(age=80)
    checked = watch.snapshot_gate(previous, NAMES, now=NOW - timedelta(seconds=75))
    path = tmp_path / watch.receipt_path(checked["ended_at"]).name
    row = {
        "status": "RESEARCH_ONLY_SHADOW_MONITOR",
        "deployment_status": "RESEARCH_ONLY_NOT_DEPLOYED", "training_eligible": False,
        "model_sha256": watch.MODEL_SHA256, "window_seconds": 60,
        "window_started_at": checked["started_at"], "window_ended_at": checked["ended_at"],
        "request_count": 90, "event_count": 2250,
        "feature_vector_sha256": watch.fingerprint(checked["features"]),
        "prediction": "NORMAL",
    }
    path.write_text(json.dumps(row), encoding="utf-8")
    calls = []
    def offline(port, route):
        calls.append((port, route))
        raise OSError("shadow unavailable")
    result = candidate_readout(offline, receipts=tmp_path, now=NOW)
    assert result["status"] == "HISTORICAL"
    assert result["recent_receipts"][0]["receipt_sha256"]
    assert "prediction" not in result
    assert calls == [(28190, READY)]
    row["model_sha256"] = "0" * 64
    path.write_text(json.dumps(row), encoding="utf-8")
    assert candidate_readout(offline, receipts=tmp_path, now=NOW)["status"] == "UNAVAILABLE"
