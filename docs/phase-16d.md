# Phase 16D — Sustained live transfer intelligence

Phase 16D is the live compatibility acceptance for the durable banking workflow
and the frozen FaultWeave model pipeline. It runs the calibrated low workload at
three requests per second through three ordered intervals:

1. 15 seconds of healthy transfer traffic;
2. 25 seconds with the payment service deliberately unavailable;
3. engineer review of every paused transfer followed by 20 seconds of healthy
   recovery traffic.

Docker structured logs are streamed into the live telemetry buffer while every
client request publishes its observed latency, HTTP status and business outcome.
The complete workload exceeds 60 seconds, so the selected recovered transfer must
overlap a valid frozen-model window.

Acceptance requires healthy baseline and recovery transfers and at least 80% of
fault-interval requests to show real customer impact through a durable review
state, HTTP 5xx response or transport failure. At least 10% must also be durable
`REQUIRES_REVIEW` cases, and every such case must complete after engineer review.
The final gate also requires `model.status=EVALUATED`, an `ABNORMAL` anomaly
decision, persisted workflow origin evidence and no use of protected fault
ground truth.

The predicted class is reported rather than forced. This lets Phase 16D detect
honest compatibility drift between the earlier research traffic and the new
durable transfer workflow instead of hiding a wrong classification.

Transient gateway responses are retained as client-observed telemetry. When a
request outcome is uncertain, the verifier reconciles it against PostgreSQL by
the original idempotency key; it never creates a replacement transfer identity
or treats an unverified HTTP error as a successful business operation.
