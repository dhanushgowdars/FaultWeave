from __future__ import annotations

from fastapi.testclient import TestClient

from scripts import live_demo_control


def test_catalog_exposes_primary_and_secondary_known_faults() -> None:
    response = TestClient(live_demo_control.app).get("/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == "5.0"
    assert len(payload["faults"]) == 9
    assert sum(item["primary_demo"] for item in payload["faults"]) == 5
    assert all(item["target"] for item in payload["faults"])
    assert payload["journey"] == [
        "gateway",
        "authentication",
        "account",
        "transaction",
        "payment",
        "ledger",
    ]


def test_start_requires_explicit_confirmation() -> None:
    response = TestClient(live_demo_control.app).post(
        "/runs",
        json={
            "fault_id": "DATABASE_HIGH_LATENCY",
            "confirmation": "wrong",
        },
    )

    assert response.status_code == 422


def test_each_guided_action_requires_its_own_confirmation() -> None:
    client = TestClient(live_demo_control.app)

    assert client.post("/cases/case-1/transactions", json={}).status_code == 422
    assert client.post("/cases/case-1/remediate", json={}).status_code == 422
    assert client.post("/cases/case-1/verify", json={}).status_code == 422


def test_control_server_refuses_non_local_binding(monkeypatch) -> None:
    monkeypatch.setattr(
        live_demo_control,
        "parse_args",
        lambda: type("Args", (), {"host": "0.0.0.0", "port": 18116})(),
    )

    try:
        live_demo_control.main()
    except SystemExit as exc:
        assert "localhost" in str(exc)
    else:
        raise AssertionError("non-local binding should be rejected")


def test_engineer_remediation_is_an_explicit_transition(tmp_path, monkeypatch) -> None:
    store = live_demo_control.DemoCaseStore(tmp_path / "cases.sqlite3")
    controller = live_demo_control.DemoController(store)
    case = store.create("SERVICE_UNAVAILABLE")
    case["status"] = "AWAITING_REVIEW"
    case["review"]["status"] = "PENDING"
    store.save(case)
    controller._case_id = case["case_id"]

    def fake_remediation(store, document, approved_by):
        document["review"]["status"] = "APPROVED"
        document["review"]["approved_by"] = approved_by
        document["status"] = "READY_TO_VERIFY"
        store.save(document)
        return document

    monkeypatch.setattr(live_demo_control, "remediate_case", fake_remediation)

    result = controller.remediate(case["case_id"], "Project engineer")

    assert result["status"] == "READY_TO_VERIFY"
    assert result["case"]["review"]["approved_by"] == "Project engineer"
