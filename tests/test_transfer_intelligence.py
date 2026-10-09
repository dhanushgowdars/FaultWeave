from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.intelligence_artifacts import FrozenIntelligenceArtifacts
from app.intelligence_live import LiveRequestObservation, LiveTelemetryBuffer
from app.transfer_intelligence import build_transfer_intelligence
from faultweave_common.logging import StructuredLogEvent


class IdentityScaler:
    def transform(self, rows):
        return rows


class FixedIsolationForest:
    def decision_function(self, rows):
        assert len(rows) == 1
        return [-0.5]


class FixedClassifier:
    def predict_proba(self, rows):
        assert len(rows) == 1
        return [[0.92, *([0.01] * 8)]]


def artifacts(feature_names: tuple[str, ...]) -> FrozenIntelligenceArtifacts:
    classes = tuple(f"FAULT_{index}" for index in range(9))
    profile = {
        "centers": [0.0] * len(feature_names),
        "scales": [1000.0] * len(feature_names),
    }
    return FrozenIntelligenceArtifacts(
        feature_names=feature_names,
        classes=classes,
        open_set_label="UNKNOWN ABNORMAL PATTERN",
        main_anomaly_threshold=0.25,
        temporal_anomaly_threshold=0.20,
        class_support_limits={name: 10.0 for name in classes},
        class_support_profiles={name: profile for name in classes},
        main_isolation_model=FixedIsolationForest(),
        main_isolation_scaler=IdentityScaler(),
        classifier_model=FixedClassifier(),
        temporal_isolation_model=FixedIsolationForest(),
        temporal_isolation_scaler=IdentityScaler(),
    )


def transfer_evidence(started: datetime) -> dict:
    failed = {
        "event_id": "event-failed",
        "sequence": 2,
        "timestamp": (started + timedelta(seconds=30)).isoformat(),
        "service": "transaction",
        "step": "payment_execution",
        "status": "FAILED",
        "detail": "Payment confirmation unavailable",
    }
    recovered = {
        "event_id": "event-recovered",
        "sequence": 4,
        "timestamp": (started + timedelta(seconds=61)).isoformat(),
        "service": "transaction",
        "step": "transfer_completed",
        "status": "SUCCEEDED",
        "detail": "Transfer completed after review",
    }
    failure = {
        "service": "payment",
        "step": "payment_execution",
        "error": {"error_type": "ConnectError", "detail": "Connection refused"},
        "funds_status": "UNKNOWN",
    }
    return {
        "id": "transfer-1",
        "state": "COMPLETED",
        "funds_status": "COMMITTED",
        "timeline": [failed, recovered],
        "case_history": {
            "failures": [failure],
            "reviews": [{"reviewed_by": "engineer-1"}],
            "recoveries": [{"status": "VERIFIED_BY_COMPLETION"}],
        },
        "investigation": {
            "observed_failure_service": "payment",
            "observed_error": failure["error"],
            "evidence_event_ids": ["event-failed"],
            "next_action": "Retain the reviewed recovery evidence.",
        },
    }


def event(at: datetime) -> StructuredLogEvent:
    return StructuredLogEvent(
        timestamp=at,
        service="gateway",
        environment="test",
        level="INFO",
        event_type="http_request_completed",
        message="transfer request completed",
        run_id="transfer-run",
        request_id=f"request-{at.timestamp()}",
        trace_id="trace-transfer",
        method="POST",
        path="/api/v1/transfers",
        status_code=200,
        latency_ms=25.0,
        outcome="success",
        success=True,
    )


def request(at: datetime) -> LiveRequestObservation:
    return LiveRequestObservation(
        request_id=f"request-{at.timestamp()}",
        trace_id="trace-transfer",
        started_at=at,
        latency_ms=25.0,
        status_code=200,
        expected_outcome=True,
        scenario="valid",
    )


def test_transfer_report_never_invents_model_result_without_window() -> None:
    start = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
    buffer = LiveTelemetryBuffer()
    report = build_transfer_intelligence(transfer_evidence(start), buffer, artifacts(("unused",)))

    assert report["operational_facts"]["observed_service"] == "payment"
    assert report["probable_origin"]["status"] == "OBSERVED_IN_WORKFLOW_EVIDENCE"
    assert report["model"]["status"] == "WINDOW_NOT_READY"
    assert report["model"]["result"] is None
    assert report["decision_authority"]["ground_truth_used"] is False


def test_transfer_report_joins_overlapping_real_model_window() -> None:
    start = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
    buffer = LiveTelemetryBuffer()
    points = [start + timedelta(seconds=value) for value in (0, 20, 40, 61)]
    buffer.ingest([event(at) for at in points], [request(at) for at in points])
    feature_names = tuple(buffer.snapshot(60)["features"])

    report = build_transfer_intelligence(transfer_evidence(start), buffer, artifacts(feature_names))

    assert report["model"]["status"] == "EVALUATED"
    assert report["model"]["result"]["status"] == "ABNORMAL"
    assert report["model"]["result"]["classification"]["label"] == "FAULT_0"
    assert "features" not in report["model"]["window"]
    assert report["operational_facts"]["recovered_after_review"] is True


def test_non_overlapping_model_window_is_not_attached_to_old_transfer() -> None:
    start = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
    buffer = LiveTelemetryBuffer()
    later = start + timedelta(minutes=10)
    points = [later + timedelta(seconds=value) for value in (0, 20, 40, 61)]
    buffer.ingest([event(at) for at in points], [request(at) for at in points])
    feature_names = tuple(buffer.snapshot(60)["features"])

    report = build_transfer_intelligence(transfer_evidence(start), buffer, artifacts(feature_names))

    assert report["model"]["status"] == "WINDOW_NOT_CORRELATED"
    assert report["model"]["result"] is None
