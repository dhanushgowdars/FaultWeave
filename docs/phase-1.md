# Phase 1 - Foundation and normal transaction flow

## Scope

Phase 1 intentionally contains no fault injection and no ML. It establishes trustworthy normal behaviour first, which is required before Phase 2 structured logging and later dataset generation.

## Architecture decisions

1. Each backend capability is an independently deployable FastAPI service.
2. Services share one PostgreSQL server for local reproducibility but own separate schemas and tables.
3. Only the gateway is a public application endpoint; other service endpoints are internal contracts.
4. All monetary values use integer minor units to avoid floating-point errors.
5. A single `X-Request-ID` is propagated through the flow; Phase 2 will include it in every JSON log.
6. Authentication uses PBKDF2 password hashing and short-lived signed demo tokens.
7. Payment is explicitly simulated and cannot transfer real money.

## Acceptance criteria

- `docker compose config` validates.
- PostgreSQL and all four services become healthy.
- `GET http://localhost:18110/health/ready` returns HTTP 200.
- A valid demo request returns `COMPLETED`, a transaction ID, payment ID, and the original request ID.
- An invalid amount is rejected at the gateway contract boundary.
- An invalid password returns HTTP 401.
- `pytest` passes.

## Database ownership

| Service | PostgreSQL schema | Table |
| --- | --- | --- |
| Authentication | `auth` | `users` |
| Transaction | `transactions` | `transaction_records` |
| Payment | `payments` | `payment_records` |

## Phase 2 boundary

Phase 2 will add a stable structured JSON logging contract, event taxonomy, correlation fields, redaction rules, and local log collection. It will not introduce faults until normal logging is verified.
