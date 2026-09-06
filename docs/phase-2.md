# Phase 2 - Structured logging and correlation

> Historical checkpoint: Phase 2 introduced schema `1.0`. Phase 2B supersedes it with
> the frozen `1.1` contract and adds Account, Ledger, `run_id`, `trace_id`, and `success`.
> New collection accepts only `1.1`; see `docs/phase-2b.md`.

## Purpose

Phase 2 creates the stable observability contract that later traffic generation, fault injection, feature engineering, and ML stages will consume. It records operational behaviour without logging request bodies, passwords, tokens, cookies, or database credentials.

## Version 1.0 event contract

Every FaultWeave application event is one JSON object on one line.

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | Yes | Contract version, currently `1.0` |
| `timestamp` | Yes | Timezone-aware UTC event time |
| `service` | Yes | `gateway`, `authentication`, `transaction`, or `payment` |
| `environment` | Yes | Runtime environment label |
| `level` | Yes | Standard severity level |
| `event_type` | Yes | Stable machine-readable event name |
| `message` | Yes | Short human-readable summary |
| `request_id` | Nullable | Cross-service correlation identifier |
| `method`, `path` | Nullable | HTTP operation without query parameters |
| `status_code` | Nullable | HTTP response status |
| `latency_ms` | Nullable | Monotonic elapsed request time |
| `outcome` | Yes | `success`, `failure`, or `unknown` |
| Entity IDs | Nullable | User, transaction, and payment correlation |
| `downstream_service` | Nullable | Dependency involved in a gateway call |
| `error_type` | Nullable | Stable error category without secret details |
| `attributes` | Yes | Non-sensitive, event-specific values |

## Event taxonomy

| Service | Event types |
| --- | --- |
| All services | `http_request_completed`, `http_request_failed`, `health_check_completed` |
| Gateway | `transaction_flow_started`, `transaction_flow_completed`, `transaction_flow_failed`, `downstream_request_completed`, `downstream_request_failed` |
| Authentication | `authentication_succeeded`, `authentication_failed` |
| Transaction | `transaction_created`, `transaction_completed`, `transaction_not_found`, `transaction_state_conflict` |
| Payment | `payment_completed` |

## Security rules

- Request and response bodies are never written to logs.
- URL query strings are excluded; only the path is recorded.
- Fields whose keys contain password, token, secret, authorization, cookie, or API-key markers are replaced with `[REDACTED]`.
- Common bearer-token, password, secret, API-key, and database-credential patterns are redacted from messages.
- Error classes may be logged; raw exception messages are not part of the event contract.
- Docker health polling is labelled `health_check_completed` so later feature engineering can exclude it from workload behaviour.

## Collection

Docker uses its rotating JSON-file driver with five files of at most 10 MB per container. Validated application events can be exported into ignored JSONL files:

```bash
python scripts/collect_logs.py --since 10m
```

The exporter removes Compose prefixes, ignores non-contract runtime lines, accepts only schema version `1.0`, and writes `data/raw/faultweave-logs-<UTC timestamp>.jsonl`.

## Acceptance criteria

- All Python tests and lint checks pass.
- One smoke transaction produces correlated JSON events from all four services.
- Every event validates against schema `1.0`.
- Request IDs match across the gateway and downstream services.
- HTTP status and latency fields are present on request events.
- Secrets do not appear in emitted events.
- Repeated log export produces valid JSONL without tracking runtime data in Git.

Run the automated container acceptance check with:

```bash
python scripts/verify_phase2.py
```
