from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import RequestIdMiddleware, request_id
from faultweave_common.schemas import HealthResponse, TransactionFlowResponse, TransactionRequest

from .orchestrator import TransactionOrchestrator

app = FastAPI(
    title="FaultWeave API Gateway",
    description="Controlled transaction simulation for the FaultWeave research environment.",
    version="0.1.0",
)
logger = configure_logging("gateway")
app.add_middleware(RequestIdMiddleware, service="gateway")


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
    current_request_id = request_id(request)
    logger.info(
        "transaction_flow_started",
        "Normal transaction flow started",
        outcome=LogOutcome.UNKNOWN,
    )
    try:
        result = await orchestrator.run(payload, current_request_id)
    except Exception as exc:
        logger.error(
            "transaction_flow_failed",
            "Normal transaction flow failed",
            outcome=LogOutcome.FAILURE,
            error_type=type(exc).__name__,
        )
        raise
    logger.info(
        "transaction_flow_completed",
        "Normal transaction flow completed",
        outcome=LogOutcome.SUCCESS,
        transaction_id=str(result.transaction_id),
        payment_id=str(result.payment_id),
    )
    return result
