from __future__ import annotations

import hashlib
from types import SimpleNamespace

from app import transfer_api
from app.main import app
from fastapi.testclient import TestClient
from faultweave_common.schemas import TransferEvidenceExport
from faultweave_common.security import create_access_token

from services.transaction.app.transfer_api import _evidence_export, _sanitize


def authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token('phase16e-customer')}"}


def evidence_contract() -> dict:
    return {
        "schema_version": "1.0",
        "transfer_id": "transfer-phase16e",
        "state": "COMPLETED",
        "created_at": "2026-10-09T09:30:00+00:00",
        "transfer": {
            "source_account_number": "FW-SOURCE",
            "destination_account_number": "FW-DEST",
            "amount_minor": 12500,
            "currency": "INR",
            "idempotency_key": "phase16e-test-key",
        },
        "funds_status": "COMMITTED",
        "requires_review": False,
        "timeline": [
            {
                "event_id": "event-1",
                "sequence": 1,
                "attempt": 1,
                "timestamp": "2026-10-09T09:30:00+00:00",
                "service": "transaction",
                "step": "transfer_received",
                "status": "SUCCEEDED",
                "detail": "Transfer received",
                "request_id": "request-1",
                "trace_id": "trace-1",
                "run_id": "run-1",
            }
        ],
        "case_history": {"failures": [], "reviews": [], "recoveries": []},
        "investigation": {"cause_status": "no_failure_observed"},
        "journal": None,
        "evidence_sources": {
            "workflow": "transactions.transfer_workflows",
            "payment": "live_database",
            "journal": "unavailable",
        },
    }


def test_export_is_schema_valid_and_deterministic() -> None:
    first = _evidence_export(evidence_contract())
    second = _evidence_export(evidence_contract())

    assert first == second
    assert TransferEvidenceExport.model_validate(first).transfer_id == "transfer-phase16e"

    changed = evidence_contract()
    changed["state"] = "REQUIRES_REVIEW"
    assert (
        _evidence_export(changed)["integrity"]["canonical_sha256"]
        != first["integrity"]["canonical_sha256"]
    )


def test_sanitizer_removes_credentials_and_pseudonymizes_reviewer() -> None:
    sanitized = _sanitize(
        {
            "authorization": "Bearer secret-token",
            "password_hash": "private-hash",
            "reviewed_by": "engineer-user-id",
            "nested": {"access_token": "secret", "detail": "safe evidence"},
        }
    )

    assert sanitized == {
        "authorization": "[REDACTED]",
        "password_hash": "[REDACTED]",
        "reviewed_by": "engineer",
        "nested": {"access_token": "[REDACTED]", "detail": "safe evidence"},
    }


def test_gateway_publishes_frozen_model_and_feature_contract(monkeypatch) -> None:
    fake = SimpleNamespace(
        feature_names=("request_count", "latency_p95_ms", "error_rate"),
        classes=("FAULT_A", "FAULT_B"),
        open_set_label="UNKNOWN ABNORMAL PATTERN",
    )
    monkeypatch.setattr(transfer_api, "get_frozen_artifacts", lambda: fake)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/contracts/transfer-intelligence",
            headers=authorization(),
        )

    assert response.status_code == 200, response.text
    contract = response.json()
    assert contract["contract_version"] == "16e.1"
    assert contract["model_contract"]["frozen"] is True
    assert contract["model_contract"]["feature_count"] == 3
    assert contract["model_contract"]["known_class_count"] == 2
    assert (
        contract["model_contract"]["feature_schema_sha256"]
        == hashlib.sha256(b"request_count\nlatency_p95_ms\nerror_rate").hexdigest()
    )


def test_contract_endpoint_requires_authentication(monkeypatch) -> None:
    monkeypatch.setattr(transfer_api, "get_frozen_artifacts", lambda: None)
    with TestClient(app) as client:
        response = client.get("/api/v1/contracts/transfer-intelligence")

    assert response.status_code == 422
