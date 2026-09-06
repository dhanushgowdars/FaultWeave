# Phase 2B - Architecture and observability freeze

## Purpose

This checkpoint freezes the service graph and event schema before any experiment or
dataset is generated. Account and Ledger are lightweight operational services, not
optional post-ML additions.

## Frozen normal flow

1. Gateway calls Authentication.
2. Gateway looks up the authenticated user's Account.
3. Gateway asks Transaction to process the simulated transaction.
4. Transaction validates the Account.
5. Transaction calls Payment.
6. Payment writes a `PAYMENT_COMPLETED` Ledger entry.
7. Transaction writes a `TRANSACTION_COMPLETED` Ledger entry and completes its record.

## Structured event schema 1.1

Every application event includes these keys. Nullable fields remain present.

| Field | Meaning |
| --- | --- |
| `schema_version` | Frozen value `1.1` |
| `timestamp` | Timezone-aware UTC event time |
| `service`, `environment`, `level` | Event producer and runtime context |
| `event_type`, `message` | Stable machine event and safe description |
| `run_id` | Experiment-wide identifier; `manual` outside an experiment |
| `request_id` | One request/transaction-flow identifier |
| `trace_id` | End-to-end call-chain identifier |
| `method`, `path`, `status_code`, `latency_ms` | HTTP evidence |
| `outcome`, `success` | Categorical and boolean result (`success` can be null for in-progress events) |
| Entity IDs | User, transaction, and payment correlation |
| `downstream_service` | Directed dependency target |
| `error_type`, `attributes` | Safe error category and non-secret details |

## Clean migration and verification

Phase 2B adds database columns and schemas. The current development volume contains only
disposable Phase 1/2 demo records, so rebuild it once before verification:

```bash
docker compose down -v
python scripts/check_ports.py
docker compose up --build -d
docker compose ps
python scripts/verify_phase2b.py
```

Then verify a clean restart without deleting the new volume:

```bash
docker compose restart
docker compose ps
python scripts/smoke_test.py
```

## Acceptance gate

- All seven containers are healthy: PostgreSQL plus six FastAPI services.
- 100 normal flows return `COMPLETED`.
- All six services occur in every flow timeline.
- All seven directed service edges are observed in downstream events.
- `run_id`, `request_id`, and `trace_id` propagate without mixing.
- No unexpected failed/error event occurs in the verification run.
- Logs contain no password, bearer token, service token, or database credential.
- Unit tests, static checks, log export, and post-restart smoke verification pass.

Only after this gate passes may Phase 3 traffic-harness work begin.
