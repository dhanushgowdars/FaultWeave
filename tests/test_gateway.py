from uuid import uuid4

from app.main import app, get_orchestrator
from fastapi.testclient import TestClient
from faultweave_common.schemas import TransactionFlowResponse, TransactionStatus


class FakeOrchestrator:
    async def run(self, payload, request_id: str, correlation) -> TransactionFlowResponse:
        assert payload.amount_minor == 12500
        assert correlation["X-Run-ID"] == "test-run-001"
        assert correlation["X-Trace-ID"] == "test-trace-001"
        return TransactionFlowResponse(
            request_id=request_id,
            account_id=uuid4(),
            transaction_id=uuid4(),
            payment_id=uuid4(),
            ledger_entry_id=uuid4(),
            status=TransactionStatus.COMPLETED,
            message="Simulated transaction completed successfully",
        )


def test_gateway_normal_flow_contract_and_request_id() -> None:
    app.dependency_overrides[get_orchestrator] = lambda: FakeOrchestrator()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/transactions",
                headers={
                    "X-Run-ID": "test-run-001",
                    "X-Request-ID": "test-request-001",
                    "X-Trace-ID": "test-trace-001",
                },
                json={
                    "username": "demo",
                    "password": "faultweave-demo",
                    "amount_minor": 12500,
                    "currency": "inr",
                    "recipient": "merchant-demo",
                },
            )
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "test-request-001"
        assert response.headers["X-Run-ID"] == "test-run-001"
        assert response.headers["X-Trace-ID"] == "test-trace-001"
        assert response.json()["request_id"] == "test-request-001"
        assert response.json()["status"] == "COMPLETED"
    finally:
        app.dependency_overrides.clear()
