# Phase 14F - Continuous evaluator and SSE stream

Phase 14F completes the transport layer between the live intelligence backend and the
Phase 15 dashboard. It does not retrain, recalibrate or otherwise modify any frozen model.

The gateway exposes a server-sent-events stream at:

```text
GET /api/v1/intelligence/stream
```

Every lifecycle evaluation that advances observable state is published to connected clients.
The stream uses monotonic event IDs, bounded per-subscriber queues and heartbeats so a slow
or disconnected browser cannot block inference.

Event names are intentionally semantic:

```text
intelligence.normal
incident.opened
incident.updated
incident.resolved
```

When a browser first connects it receives a `snapshot` event containing the currently active
incident, if any, and the most recently published stream event. The dashboard can therefore
recover state after refresh without treating the SSE channel as durable storage.

The evaluator itself remains the existing deterministic Phase 14E lifecycle:

```text
POST /api/v1/intelligence/live/evaluate
```

For continuous host-side evaluation run:

```bash
python scripts/live_intelligence_evaluator.py
```

The evaluator polls the lifecycle endpoint at a low cadence. Duplicate polling is safe:
Phase 14E advances temporal persistence only on a new 10-second event-time bucket and allows
same-bucket refresh only when observable telemetry has advanced.

Phase 14F is intentionally split into two integration gates:

1. **14F1 - SSE + continuous evaluator**: publish already-verified incident lifecycle state
   to live clients.
2. **14F2 - continuous client request telemetry**: instrument the FaultWeave traffic source
   so client-observed request records are emitted during the run, not only after a verifier
   finishes.

14F2 is required before Phase 14 is declared complete because frozen inference now correctly
requires the same client-side request measurement semantics used by Phase 11C training.

The instrumented traffic source is:

```bash
python scripts/live_traffic.py --profile low
```

Each completed request is observed at the traffic source and queued into bounded, short-lived
batches posted to `/api/v1/intelligence/telemetry`. These observations contain request timing,
status/outcome and workload scenario only. They contain no fault family, injected interval,
expected origin, dataset split or training label. The existing Docker-log bridge continues to
supply structured service/dependency events independently.

A complete local live run therefore uses:

```text
python scripts/live_telemetry_bridge.py
python scripts/live_traffic.py --profile low
python scripts/live_intelligence_evaluator.py
```

Phase 17 may wrap these processes in a single demo launcher; keeping them separate in Phase 14
makes each telemetry boundary independently testable.

No Kafka, Redis pub/sub or external broker is introduced. SSE is process-local and appropriate
for the single-gateway research/demo deployment. Gateway restart clears the stream sequence and
in-memory incident history; durable incident storage remains outside Phase 14.
