# Phase 19B — model-ready blind random challenge

Phase 19A proved that a random accepted condition could stay sealed until after investigation,
but its single transfer correctly returned `WINDOW_NOT_READY`: one request cannot provide the
60 seconds of observable evidence required by the frozen Phase 11D model. Phase 19B closes that
gap without changing or retraining the model.

## Execution contract

One blind run now performs three real workload phases:

1. 15 seconds of healthy baseline traffic;
2. 30 seconds with the committed challenge condition active; and
3. 20 seconds after the condition is cleared.

The workload uses the original `/api/v1/transactions` research path at three requests per
second. Those transactions are isolated simulated records; they do not debit or credit the
two accounts shown in the judge-facing durable transfer. The visible transfer still moves
funds exactly once and retains its existing PostgreSQL, idempotency, journal, review and
recovery checks.

Every workload request publishes its actual status, latency and transport outcome as
client-observed telemetry. The existing telemetry bridge collects the structured events that
the six services actually emitted. No fault label, expected class or selected scenario is
added to those observations.

## Fail-closed model readiness

The backend blocks the challenge before fault execution if current bridged service events are
not available. After the three phases, it also requires a complete 60-second window containing
both client-observed requests and real service events. A blind analysis is rejected unless the
frozen model returns `EVALUATED` for that window.

This boundary prevents all of the following:

- inventing model output from one transfer;
- silently using stale telemetry;
- substituting protected ground truth for prediction;
- claiming that workflow evidence and model evidence have the same authority; and
- moving customer funds repeatedly merely to fill a feature window.

The model's anomaly decision and known-class prediction are compared with the committed
condition only after reveal. A mismatch remains visible; the verifier does not rewrite it into
a success.

## Live acceptance

Rebuild the gateway, start the complete Docker stack and run:

```powershell
python -m scripts.verify_phase19b
```

The verifier starts the telemetry bridge itself, creates one sealed random case, waits for the
real sustained workload, requires an evaluated 60-second frozen-model result, verifies the
public SHA-256 reveal, and performs explicit same-transfer recovery when a fault was selected.
The run takes approximately 70–90 seconds.
