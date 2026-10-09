# Phase 16E — API contract and evidence export

Phase 16E freezes the backend interfaces that the investigation assistant and
frontend will consume. It does not change model training, fault labels, transfer
semantics or the six-service architecture.

## Public contracts

- `GET /api/v1/transfers` returns authorized, paginated transfer history.
- `GET /api/v1/transfers/search` performs exact search by transfer ID, request
  ID, trace ID or idempotency key.
- `GET /api/v1/engineer/review-queue` returns oldest-first durable cases waiting
  for review and rejects customer tokens.
- `GET /api/v1/transfers/{id}/evidence` returns the normalized `1.0` evidence
  contract with ordered events, case history, investigation facts and journal.
- `GET /api/v1/transfers/{id}/evidence/export` returns the same sanitized
  evidence with a deterministic SHA-256 checksum.
- `GET /api/v1/contracts/transfer-intelligence` publishes the frozen API,
  feature-schema and model-family contract without exposing model internals.

The existing transfer detail and intelligence routes remain available for
backward compatibility.

## Security and evidence rules

Customer history, search, evidence and exports are limited to the authenticated
owner. Engineers can inspect all transfers and access the review queue. Missing
or unauthorized transfer IDs return `404`, preventing ownership discovery.

Exports redact credential-shaped fields, bearer values and reviewer identities.
The operational database retains the original reviewer identity for audit; the
portable export exposes only the reviewer role. Evidence event IDs in failure
history must refer to actual `FAILED` timeline events.

## Acceptance gate

The unit and integration gates verify schemas, pagination, exact search,
authorization, chronological ordering, failure citations, redaction and checksum
stability. The live verifier creates a real transfer, exports its evidence,
restarts the transaction service and requires the post-restart evidence and hash
to be identical. Passing this gate means PostgreSQL, not process memory, is the
authoritative evidence store.
