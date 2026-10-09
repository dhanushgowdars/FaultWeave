# Phase 16C — Transfer intelligence compatibility

Phase 16C connects the durable banking-transfer workflow to FaultWeave's frozen
intelligence pipeline without changing the Phase 6 dataset, feature schema,
trained artifacts or thresholds.

## Evidence contract

`GET /api/v1/transfers/{transfer_id}/intelligence` returns two deliberately
separate channels:

1. **Operational facts** come from PostgreSQL-backed workflow events, failure
   history, engineer reviews, recoveries and the balanced journal. These facts
   are authoritative for the transfer state, the observed failing workflow step
   and whether funds moved.
2. **Machine-learning evidence** comes only from the existing frozen 60-second
   rolling telemetry window. It is system-level anomaly/classification evidence,
   not a replacement for transaction truth.

The ML result is attached only when the validated window overlaps the transfer's
ordered event interval. Otherwise the endpoint returns `WINDOW_NOT_READY` or
`WINDOW_NOT_CORRELATED` with no invented label.

Protected fault ground truth is never read by this endpoint. Raw feature vectors
are also not returned; the response includes the frozen feature-schema hash and
window provenance when inference runs.

## Judge-facing interpretation

- “What broke?” is answered from cited persisted workflow event IDs.
- “Did money move?” is answered from funds status and the debit/credit journal.
- “Was the wider system anomalous?” is answered independently by the frozen
  Isolation Forest, XGBoost classifier and open-set rejection policy.
- “Was it recovered?” requires a stored engineer review and a later completion
  event for the same idempotent transfer identity.

This separation prevents a plausible ML label from overriding contradictory
banking evidence and prevents an isolated transaction from being disguised as a
complete 60-second model window.
