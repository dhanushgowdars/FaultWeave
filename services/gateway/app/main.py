from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from faultweave_common.logging import LogOutcome, configure_logging
from faultweave_common.middleware import (
    CorrelationMiddleware,
    request_correlation_headers,
    request_id,
)
from faultweave_common.schemas import HealthResponse, TransactionFlowResponse, TransactionRequest

from .intelligence_artifacts import (
    ArtifactLoadError,
    FrozenIntelligenceArtifacts,
    get_frozen_artifacts,
)
from .intelligence_inference import (
    InferenceInputError,
    IntelligenceInferenceRequest,
    run_intelligence_inference,
)
from .orchestrator import TransactionOrchestrator

app = FastAPI(
    title="FaultWeave API Gateway",
    description="Controlled transaction simulation for the FaultWeave research environment.",
    version="0.2.0",
)
logger = configure_logging("gateway")
app.add_middleware(CorrelationMiddleware, service="gateway")


def get_orchestrator() -> TransactionOrchestrator:
    return TransactionOrchestrator()


def get_intelligence_artifacts() -> FrozenIntelligenceArtifacts:
    return get_frozen_artifacts()


@app.exception_handler(ArtifactLoadError)
async def intelligence_artifact_error(_request: Request, exc: ArtifactLoadError) -> JSONResponse:
    logger.error(
        "intelligence_artifacts_unavailable",
        "Frozen intelligence artifacts are unavailable",
        outcome=LogOutcome.FAILURE,
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=503,
        content={
            "status": "not_ready",
            "reason": "frozen intelligence artifacts unavailable",
        },
    )


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="gateway", status="up")


@app.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    return HealthResponse(service="gateway", status="up")


@app.get("/api/v1/intelligence/ready")
async def intelligence_ready(
    artifacts: FrozenIntelligenceArtifacts = Depends(get_intelligence_artifacts),
) -> dict[str, object]:
    return dict(artifacts.readiness())


@app.post("/api/v1/intelligence/infer")
async def intelligence_infer(
    payload: IntelligenceInferenceRequest,
    artifacts: FrozenIntelligenceArtifacts = Depends(get_intelligence_artifacts),
) -> dict[str, object]:
    try:
        result = run_intelligence_inference(artifacts, payload)
    except InferenceInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "intelligence_inference_completed",
        "Frozen intelligence inference completed",
        outcome=LogOutcome.SUCCESS,
        attributes={
            "window_seconds": payload.window_seconds,
            "status": result["status"],
        },
    )
    return result


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
        result = await orchestrator.run(
            payload,
            current_request_id,
            request_correlation_headers(request),
        )
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
