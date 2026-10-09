# Phase 16A: durable transfer backend

Status: integrated backend implemented; validate PostgreSQL and the Docker deployment before committing.

## Execution and persistence

The public login endpoint calls Authentication. A bearer-authenticated transfer goes through Gateway, Transaction, Payment, Account, and Ledger. Transaction and Payment persist their own workflow state and ordered events. Account commits both balances and a debit/credit journal in one database transaction. Ledger independently reads that committed account journal, validates the pair and persists an idempotent posting. Transaction reports COMPLETED only after Payment confirms both funds movement and ledger posting.

PostgreSQL transaction-scoped advisory locks serialize operations for a user/idempotency key. Ordered account row locks protect competing transfers against the same balances. Workflow events commit separately from the operation lock, so case inspection can read progress while a request is running. After a worker crash, a reviewed retry can resume an unfinished workflow once PostgreSQL releases the lock.

This is a simulated internal bank transfer. The demo sender has seeded funds; new receiver accounts start at zero. No external bank network, real settlement, interest, loans, or production banking compliance is claimed. The original dataset-generating `/api/v1/transactions` route remains available for its frozen research baseline.

## Failure and review contract

- A failed dependency sets REQUIRES_REVIEW and records the observed stage/error. No automatic business retry or automatic refund runs.
- A lost response is not proof that funds did not move. The case uses UNKNOWN until an idempotent replay confirms the account outcome.
- If Account confirms movement but Ledger fails, the case reports COMMITTED funds and waits for review.
- Reposting the same instruction reads its saved outcome. A changed payload with the same key is rejected.
- The explicit resume API requires a nonblank review note of at least ten characters. It uses the original identities and cannot double debit a completed account transfer.
- Review authorisation currently uses the authenticated owner of the demo case. A separate engineer role and multi-user administration are future work; this is not production role separation.
- Restoring a dependency and retrying is explicit. The resume endpoint does not start Docker containers or remove an injected fault for the user.

## APIs

| Method | Path | Result |
| --- | --- | --- |
| POST | `/api/v1/auth/login` | Existing authentication service issues token |
| GET | `/api/v1/accounts/{account_number}` | Owned account and balance |
| POST | `/api/v1/accounts` | New empty demo receiver account |
| POST | `/api/v1/transfers` | Durable transfer workflow and current state |
| GET | `/api/v1/transfers/by-key/{key}` | Inspect/poll a transfer during the original request |
| GET | `/api/v1/transfers/{id}` | Saved events, current payment evidence, funds status and investigation |
| POST | `/api/v1/transfers/{id}/resume` | Explicit reviewed retry using original transfer |

Transfer fields: `source_account_number`, `destination_account_number`, positive integer `amount_minor`, `currency`, and `idempotency_key`. Resume body: `{"note": "Reviewed the failure evidence and restored the dependency"}`.

HTTP completion and business completion are distinct: read `state`, `funds_status`, and the stored failed events. A 200 response can successfully return a REQUIRES_REVIEW case. The case investigation names the observed failed dependency, references evidence event IDs, and leaves deeper root cause unconfirmed when the evidence cannot establish it. Timeline timestamps and per-service sequence numbers preserve actual recorded events, including earlier failed attempts.

## Evidence storage

- `transactions.transfer_workflows`: transfer instruction, state, events, payment snapshot.
- `payments.transfer_workflows`: payment state, events, account and ledger confirmations.
- `accounts.transfers` and `accounts.transfer_entries`: committed funds movement and paired journal.
- `ledger.transfer_postings`: verified immutable copy of the account journal.
- Normal structured service logs continue through Docker with request/run/trace correlation.

The workflow events are durable database evidence, not a copy of every Docker log line. Case reads identify whether payment evidence is live, last saved, or unavailable. The smoke script exports both success and rejection cases under `data/experiments/transfer-verification/`. This path is already ignored as generated experiment data. Database records live in the existing PostgreSQL volume; source pushes do not back up that volume or the research datasets.

## Model boundary

No model is retrained by this package. The old model's feature windows, routing topology and workload differ from the new transfer flow. Single transfers do not justify manufacturing a 60-second model prediction. New cases explicitly report model_prediction.status = NOT_EVALUATED. Phase 16C must validate revised observable features and the frozen models before attaching predictions; collect/version new data and retrain only if that evaluation shows it is necessary.

## Verification

1. Install `requirements-dev.txt`, run Ruff and the full Pytest suite.
2. Run `docker compose up -d --build --wait`.
3. Run `python scripts/verify_phase16_postgres.py`. It creates random `fwtest_*` schemas, exercises all real service handlers with PostgreSQL persistence and production advisory/row locks, then removes those test schemas. It does not clear production schemas.
4. Run `python scripts/smoke_transfer.py` to exercise actual Docker HTTP routes and save case evidence.
5. Run `python scripts/smoke_test.py` to check the original research transaction flow.

Default local integration tests use SQLite with a test-only mutex adapter. The competing-balance test is skipped without FW_TRANSFER_TEST_DATABASE_URL. The PostgreSQL verification script enables it. Network outages and lost responses in the automated suite are deliberate transport faults; the database operations and service route handlers are real.
