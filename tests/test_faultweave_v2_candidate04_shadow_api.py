"""Offline tests; no frozen model, Docker, banking stack, or network required."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts import faultweave_v2_candidate04_shadow_api as m

TEST_NAMES = tuple(f"feature_{index:02d}" for index in range(65))
MODEL_HASH = "a" * 64


@pytest.fixture
def fixture(monkeypatch):
    def stub_predict(_model, _iso, rows):
        assert tuple(rows[0]["features"]) == TEST_NAMES
        assert len(rows) == 1
        return [{
            "prediction": "UNKNOWN ABNORMAL PATTERN",
            "reason": "ISO_AND_OUTSIDE_KNOWN_SUPPORT_RESCUE",
            "binary_fault": False,
            "iso_anomaly": True,
            "nearest_predicted_class": "DATABASE_HIGH_LATENCY",
            "known_distance": 9.0,
            "known_radius": 1.0,
            "normal_distance": 3.0,
            "normal_radius": 2.0,
            "isolation_score": 0.3,
        }]

    monkeypatch.setattr(m, "_feature_names", lambda: TEST_NAMES)
    monkeypatch.setattr(m, "_frozen_artifacts", lambda: (Path("dummy.joblib"), MODEL_HASH))
    monkeypatch.setattr(m, "_load_predictor", lambda: (stub_predict, {}, {}))
    return TestClient(m.app)


def vector():
    return {name: float(i) for i, name in enumerate(TEST_NAMES)}


def test_health_is_research_only(fixture):
    assert fixture.get("/health").json()["type"] == "research_shadow_service"


def test_ready_is_not_a_deployment_claim(fixture):
    response = fixture.get("/api/v2/candidate04/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["feature_count"] == 65
    assert body["deployment_status"] == "RESEARCH_ONLY_NOT_DEPLOYED"
    assert body["gateway_replaced"] is False


def test_schema_is_exact_order(fixture):
    response = fixture.get("/api/v2/candidate04/schema")
    assert response.status_code == 200
    assert response.json()["feature_names"] == list(TEST_NAMES)


def test_inference_uses_only_ordered_numeric_features(fixture):
    values = dict(reversed(list(vector().items())))
    response = fixture.post("/api/v2/candidate04/infer", json={"window_seconds": 60, "features": values})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"]["prediction"] == "UNKNOWN ABNORMAL PATTERN"
    assert body["probable_origin"] is None
    assert body["input_provenance"] == "OPERATOR_SUBMITTED_FEATURES_NOT_INDEPENDENTLY_AUDITED"
    assert body["training_performed"] is False
    assert body["automatic_recovery"] is False
    assert "candidate03_baseline_prediction" not in body["decision"]


@pytest.mark.parametrize("change", [
    lambda obj: obj.update(window_seconds=30),
    lambda obj: obj.update(window_seconds=10),
    lambda obj: obj.update(window_seconds=60.0),
    lambda obj: obj.update(window_seconds=True),
    lambda obj: obj["features"].pop(TEST_NAMES[0]),
    lambda obj: obj["features"].update(label=1.0),
    lambda obj: obj["features"].update({TEST_NAMES[5]: True}),
    lambda obj: obj["features"].update({TEST_NAMES[5]: "2.0"}),
    lambda obj: obj.update(label="KNOWN_FAULT"),
])
def test_bad_inputs_are_rejected(fixture, change):
    payload = {"window_seconds": 60, "features": vector()}
    change(payload)
    response = fixture.post("/api/v2/candidate04/infer", json=payload)
    assert response.status_code == 422, response.text


def test_model_not_ready_fails_closed(fixture, monkeypatch):
    def broken():
        raise RuntimeError("changed frozen artifact")
    monkeypatch.setattr(m, "_frozen_artifacts", broken)
    assert fixture.get("/api/v2/candidate04/ready").status_code == 503
    assert fixture.post("/api/v2/candidate04/infer", json={"window_seconds": 60, "features": vector()}).status_code == 503


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def scored_pair(base, rid: str, prediction="UNKNOWN ABNORMAL PATTERN", score_prediction=None):
    receipt_path = base / f"{rid}.prediction.json"
    receipt = {
        "schema_version": "faultweave-candidate04-evaluation-prediction.1",
        "run_id": rid,
        "model_sha256": MODEL_HASH,
        "decision": {"prediction": prediction},
    }
    write_json(receipt_path, receipt)
    receipt_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    scored_prediction = prediction if score_prediction is None else score_prediction
    score = {
        "schema_version": "faultweave-candidate04-evaluation-reveal.1",
        "run_id": rid,
        "model_sha256": MODEL_HASH,
        "prediction_sha256": receipt_digest,
        "prediction": scored_prediction,
        "unknown_rejected": scored_prediction == "UNKNOWN ABNORMAL PATTERN",
        "actual_fault_family": "TEST_UNKNOWN_FAMILY",
        "recovery_verified": True,
    }
    write_json(base / f"{rid}.score.json", score)
    return receipt_path


def test_read_only_evaluation_summary(fixture, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "SCORES_ROOT", tmp_path)
    scored_pair(tmp_path, "v2blind-1234567890abcdef")
    scored_pair(tmp_path, "v2blind-fedcba0987654321", prediction="NORMAL")
    response = fixture.get("/api/v2/candidate04/evaluations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert body["correct_unknown"] == 1
    assert body["generalization_claim"] == "INSUFFICIENT_SAMPLE_SIZE"
    assert all(item["evaluation_only"] for item in body["results"])


def test_score_tampering_rejected(fixture, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "SCORES_ROOT", tmp_path)
    path = scored_pair(tmp_path, "v2blind-1234567890abcdef")
    write_json(path, {"schema_version": "corrupted"})
    response = fixture.get("/api/v2/candidate04/evaluations")
    assert response.status_code == 503


def test_score_pair_mismatch_rejected(fixture, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "SCORES_ROOT", tmp_path)
    scored_pair(tmp_path, "v2blind-1234567890abcdef", score_prediction="NORMAL")
    assert fixture.get("/api/v2/candidate04/evaluations").status_code == 503


def test_wrong_host_rejected(fixture):
    response = fixture.get("/health", headers={"host": "evil.example"})
    assert response.status_code == 400


def test_unknown_endpoint_does_not_support_fault_control(fixture):
    assert fixture.post("/api/v2/candidate04/inject", json={}).status_code == 404
    assert fixture.post("/api/v2/candidate04/retrain", json={}).status_code == 404


def test_shadow_does_not_claim_origin_service(fixture):
    result = fixture.post("/api/v2/candidate04/infer", json={"window_seconds": 60, "features": vector()}).json()
    assert result["origin_status"] == "NOT_ESTIMATED_BY_CANDIDATE04"
    assert "confidence" not in result["decision"]
