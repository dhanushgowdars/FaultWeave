from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from faultweave_common.middleware import RequestIdMiddleware, request_id
from faultweave_common.schemas import HealthResponse, TransactionFlowResponse, TransactionRequest

from .orchestrator import TransactionOrchestrator

app = FastAPI(
    title="FaultWeave API Gateway",
    description="Controlled transaction simulation for the FaultWeave research environment.",
    version="0.1.0",
)
app.add_middleware(RequestIdMiddleware)


def get_orchestrator() -> TransactionOrchestrator:
    return TransactionOrchestrator()


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="gateway", status="up")


@app.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    return HealthResponse(service="gateway", status="up")


@app.post("/api/v1/transactions", response_model=TransactionFlowResponse)
async def normal_transaction(
    payload: TransactionRequest,
    request: Request,
    orchestrator: TransactionOrchestrator = Depends(get_orchestrator),
) -> TransactionFlowResponse:
    return await orchestrator.run(payload, request_id(request))
