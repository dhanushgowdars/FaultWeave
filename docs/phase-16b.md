# Phase 16B: controlled transfer faults and engineer review

Phase 16B connects a small, defensible subset of the existing controlled-fault
framework to the durable transfer path. It does not advertise every research
fault as an interactive transfer demo. Each advertised case must produce the
expected failure stage, persist evidence, stop for review, recover with the same
transfer identity, and prove a balanced debit/credit result.

## Roles and control boundary

The customer authenticates, owns both demo accounts, and starts the transfer.
Authentication also seeds a separate demo engineer identity. Only a token with
the `engineer` role can call the transfer resume endpoint. Transaction records
the engineer subject, decision, note, event, timestamp and attempt before any
retry begins. It delegates the resumed downstream work under the original
customer identity, so account ownership remains enforced.

Default local credentials are `demo` / `faultweave-demo` and `engineer` /
`faultweave-engineer`. They are development defaults and must be replaced in a
deployed environment. The current system has one demo engineer role; production
RBAC, approval separation and an external identity provider remain outside this
research prototype.

Fault activation remains a local operator capability through the existing
exclusive file lease and Docker adapter. It is not exposed as an unauthenticated
public banking endpoint. The active lease is bounded, exclusive, expires, and is
removed during cleanup even if execution fails.

## Advertised cases

| Case | Observed failure stage | Funds interpretation |
| --- | --- | --- |
| Payment unavailable | `payment_execution` | `UNKNOWN` until reconciled |
| Ledger unavailable | `ledger_posting` | `COMMITTED`; never debit again |
| Account response timeout | `account_commit` | `UNKNOWN`; reconcile by key |
| Ledger response timeout | `ledger_posting` | `COMMITTED`; retry posting only |
| Account error response | `account_commit` | `UNKNOWN`; inspect before retry |

`UNKNOWN` is deliberately conservative. A missing response is not evidence that
a write did not happen. Account and Ledger idempotency make the reviewed retry
safe. Ledger failures retain `COMMITTED` because Account already returned the
durable transfer and its two journal entry identifiers.

## Stored case evidence

Transaction and Payment append failure history rather than overwriting it. A
reviewed recovery contains all three evidence groups:

1. `failure_history`: observed stage, service, error, event and funds status.
2. `review_history`: engineer identity, note, decision, event and attempt.
3. `recovery_history`: successful completion event for the same transfer.

The case API merges Transaction and Payment histories into `case_history` and
returns an ordered timeline. `cause_status` changes to
`recovered_after_review` only after Account and Ledger confirmations are both
present. Removing a fault or restarting a container does not resolve a case.

Observable evidence and protected expected ground truth are written to separate
files under `data/experiments/transfer-fault-cases/<run-id>/`. The manifest
records SHA256 checksums. Ground truth is marked excluded from inference, and
the acceptance verifier rejects fault identifiers found in observable evidence.
These generated records are ignored by Git and are not automatically backed up
by a source push.

## Operation

List cases:

`python -m scripts.phase16_transfer_fault_case list`

Start one case and leave it paused:

`python -m scripts.phase16_transfer_fault_case start --case ledger-service-unavailable`

The command prints a run identifier. After inspecting the case, use the printed
resume command with an engineer review note. For acceptance, run
`python -m scripts.verify_phase16b`; it executes all five cases against live
Docker services and validates the isolated artifacts.
