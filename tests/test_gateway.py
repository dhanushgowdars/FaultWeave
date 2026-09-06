from uuid import uuid4

from app.main import app, get_orchestrator
from fastapi.testclient import TestClient
from faultweave_common.schemas import TransactionFlowResponse, TransactionStatus


class FakeOrchestrator:
    async def run(self, payload, request_id: str) -> TransactionFlowResponse:
        assert payload.amount_minor == 12500
        return TransactionFlowResponse(
            request_id=request_id,
            transaction_id=uuid4(),
            payment_id=uuid4(),
            status=TransactionStatus.COMPLETED,
            message="Simulated transaction completed successfully",
        )


def test_gateway_normal_flow_contract_and_request_id() -> None:
    app.dependency_overrides[get_orchestrator] = lambda: FakeOrchestrator()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/transactions",
                headers={"X-Request-ID": "test-request-001"},
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
        assert response.json()["request_id"] == "test-request-001"
        assert response.json()["status"] == "COMPLETED"
    finally:
        app.dependency_overrides.clear()
