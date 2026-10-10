# Phase 17 — persisted demo orchestration backend

Phase 17 turns the verified transfer, evidence, model and assistant APIs into one
strictly ordered backend demonstration. It does not add another banking service and it
does not invent a second source of financial truth.

## Authority boundaries

- PostgreSQL transfer, payment, account and ledger records remain authoritative.
- The Phase 17 session file stores only demo sequencing, selected scenario and snapshots.
- The selected fault identifier is never sent to the frozen model or grounded assistant.
- Only the engineer role can use the demo-control API.
- The assistant is read-only and cannot arm faults, approve recovery or resume transfers.
- A dependency becoming healthy does not recover a paused transfer automatically.

## Ordered state machine

1. `PREPARED` — scenario and transfer input saved; nothing has executed.
2. `FAULT_ARMED` — one bounded, run-scoped fault is active; no transfer has executed.
3. `AWAITING_REVIEW` — the transfer failed, evidence is durable and the fault is cleared.
4. `APPROVED` — an engineer reviewed evidence and approved the same-transfer resume.
5. `RECOVERED` — the same transfer completed once and balances plus journal reconcile.

The normal case moves directly from `PREPARED` to `COMPLETED`.

## API actions

| Method and route | Meaning |
|---|---|
| `GET /api/v1/demo/contract` | Read the frozen orchestration contract. |
| `GET /api/v1/demo/scenarios` | List one normal and five controlled cases. |
| `POST /api/v1/demo/sessions` | Prepare a case without executing anything. |
| `POST /api/v1/demo/sessions/{id}/arm` | Arm the selected fault only. |
| `POST /api/v1/demo/sessions/{id}/execute` | Execute one real durable transfer. |
| `POST /api/v1/demo/sessions/{id}/analyze` | Join evidence, model channel and assistant output. |
| `POST /api/v1/demo/sessions/{id}/approve` | Save the human decision without recovery. |
| `POST /api/v1/demo/sessions/{id}/recover` | Resume and financially verify the same transfer. |
| `GET /api/v1/demo/sessions/{id}` | Restore the ordered session after restart. |

## Primary demonstration

`ledger-service-unavailable` is the strongest judge-facing case because it shows an
important distributed-systems distinction: Account can commit the debit and credit while
Payment still lacks Ledger confirmation. FaultWeave must not retry the money movement.
It preserves `COMMITTED` funds evidence, waits for a human decision, then resumes the
original idempotent transfer so only the missing confirmation is completed.

## Acceptance gate

`python -m scripts.verify_phase17` executes a normal transfer and the primary ledger
fault, proves recovery is blocked before review, restarts Gateway to prove session
persistence, runs grounded analysis, persists approval, resumes the same transfer and
checks exact balances, a debit/credit journal pair and evidence-export integrity.
