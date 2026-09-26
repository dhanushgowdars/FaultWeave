from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
from .intelligence_incidents import (
    LiveIncidentManager,
    get_live_incident_manager,
)
from .intelligence_live import (
    LiveTelemetryBatch,
    LiveTelemetryBuffer,
    LiveWindowNotReady,
    get_live_telemetry_buffer,
)
from .intelligence_stream import (
    STREAM_HEARTBEAT_SECONDS,
    IntelligenceStreamMessage,
    LiveIntelligenceStream,
    encode_snapshot,
    encode_sse,
    get_live_intelligence_stream,
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


@app.post("/api/v1/intelligence/telemetry")
async def intelligence_telemetry(
    payload: LiveTelemetryBatch,
    buffer: LiveTelemetryBuffer = Depends(get_live_telemetry_buffer),
) -> dict[str, object]:
    summary = buffer.ingest(payload.events, payload.requests)
    logger.info(
        "intelligence_telemetry_ingested",
        "Live telemetry batch ingested",
        outcome=LogOutcome.SUCCESS,
        attributes={
            "accepted": summary["accepted"],
            "ignored": summary["ignored"],
            "duplicates": summary["duplicates"],
            "accepted_requests": summary["accepted_requests"],
        },
    )
    return {"status": "accepted", **summary}


def _live_snapshot_or_409(
    buffer: LiveTelemetryBuffer,
    window_seconds: int,
    *,
    require_client_requests: bool = False,
) -> dict[str, object]:
    if window_seconds not in {10, 60}:
        raise HTTPException(status_code=422, detail="window_seconds must be 10 or 60")
    try:
        return buffer.snapshot(
            window_seconds, require_client_requests=require_client_requests
        )
    except LiveWindowNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/intelligence/live/features/{window_seconds}")
async def intelligence_live_features(
    window_seconds: int,
    buffer: LiveTelemetryBuffer = Depends(get_live_telemetry_buffer),
) -> dict[str, object]:
    return _live_snapshot_or_409(buffer, window_seconds)


@app.get("/api/v1/intelligence/live/infer/{window_seconds}")
async def intelligence_live_infer(
    window_seconds: int,
    buffer: LiveTelemetryBuffer = Depends(get_live_telemetry_buffer),
    artifacts: FrozenIntelligenceArtifacts = Depends(get_intelligence_artifacts),
) -> dict[str, object]:
    snapshot = _live_snapshot_or_409(
        buffer, window_seconds, require_client_requests=True
    )
    payload = IntelligenceInferenceRequest(
        window_seconds=window_seconds,
        features=snapshot["features"],
    )
    try:
        result = run_intelligence_inference(artifacts, payload)
    except InferenceInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"window": snapshot, "result": result}


@app.post("/api/v1/intelligence/live/evaluate")
async def intelligence_live_evaluate(
    buffer: LiveTelemetryBuffer = Depends(get_live_telemetry_buffer),
    artifacts: FrozenIntelligenceArtifacts = Depends(get_intelligence_artifacts),
    manager: LiveIncidentManager = Depends(get_live_incident_manager),
    stream: LiveIntelligenceStream = Depends(get_live_intelligence_stream),
) -> dict[str, object]:
    try:
        result = manager.evaluate(buffer, artifacts)
    except LiveWindowNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "intelligence_incident_evaluated",
        "Live incident lifecycle evaluated",
        outcome=LogOutcome.SUCCESS,
        attributes={"status": result["status"]},
    )
    stream_event = {
        "NORMAL": "intelligence.normal",
        "INCIDENT_OPENED": "incident.opened",
        "INCIDENT_UPDATED": "incident.updated",
        "INCIDENT_REFRESHED": "incident.updated",
        "INCIDENT_RESOLVED": "incident.resolved",
    }.get(str(result["status"]))
    if stream_event is not None:
        stream.publish(stream_event, result)
    return result


@app.get("/api/v1/intelligence/stream")
async def intelligence_stream(
    request: Request,
    manager: LiveIncidentManager = Depends(get_live_incident_manager),
    stream: LiveIntelligenceStream = Depends(get_live_intelligence_stream),
) -> StreamingResponse:
    async def event_source():
        token: int | None = None
        try:
            try:
                token, queue = stream.subscribe()
            except RuntimeError as exc:
                yield encode_sse(
                    IntelligenceStreamMessage(
                        event_id=stream.current_event_id(),
                        event="stream.error",
                        data={"detail": str(exc)},
                    )
                )
                return

            yield encode_snapshot(
                event_id=stream.current_event_id(),
                active_incident=manager.current_incident(),
                latest=stream.latest(),
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(
                        queue.get(),
                        timeout=STREAM_HEARTBEAT_SECONDS,
                    )
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield encode_sse(message)
        finally:
            if token is not None:
                stream.unsubscribe(token)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/intelligence/incidents/current")
async def intelligence_current_incident(
    manager: LiveIncidentManager = Depends(get_live_incident_manager),
) -> dict[str, object]:
    incident = manager.current_incident()
    return {"active": incident is not None, "incident": incident}


@app.get("/api/v1/intelligence/incidents")
async def intelligence_incidents(
    manager: LiveIncidentManager = Depends(get_live_incident_manager),
) -> dict[str, object]:
    rows = manager.incidents()
    return {"count": len(rows), "incidents": rows}


@app.get("/api/v1/intelligence/incidents/{incident_id}")
async def intelligence_incident_detail(
    incident_id: str,
    manager: LiveIncidentManager = Depends(get_live_incident_manager),
) -> dict[str, object]:
    incident = manager.incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return incident


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
