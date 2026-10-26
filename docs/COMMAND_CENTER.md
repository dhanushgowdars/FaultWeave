# FaultWeave Command Center

## Purpose

FaultWeave is an evidence-first incident intelligence system for a banking
microservice workflow. The Command Center combines authenticated backend
operations, persisted transfer evidence, telemetry, the frozen Candidate 04
research model, evidence-backed fault localization, grounded explanations and
engineer-controlled recovery in one interface.

## Implemented service path

`Gateway → Authentication → Transaction → Payment → Account → Ledger → PostgreSQL`

The financial state is determined from persisted workflow, account and ledger
records. Model predictions never replace transaction evidence.

## Candidate 04 record

- Model: `v2-verified126-candidate-04`
- Model SHA256: `7bbf6c2b0ba57d40a45d1ac633c3b50ecfe9ed1b2ea6354d460a32fd120da0be`
- Verified dataset: 126 admitted experiments
- Split: 84 training, 21 validation, 21 test
- Feature contract: 65 observable values from an exact 60-second window
- Development evaluation: 21/21 on the small development evaluation
- Sealed unknown evaluation: 2/2 reserved runs rejected as unknown
- Deployment status: `RESEARCH_ONLY_NOT_DEPLOYED`

The 21-case and two-case results describe those exact evaluations. They are not
presented as general-world accuracy or production-readiness estimates.

## Nine trained families

1. `AUTHENTICATION_FAILURE_BURST`
2. `CONNECTION_POOL_EXHAUSTION`
3. `DATABASE_HIGH_LATENCY`
4. `DATABASE_LOCK_CONTENTION`
5. `DATABASE_UNAVAILABLE`
6. `DOWNSTREAM_ERROR_BURST`
7. `DOWNSTREAM_TIMEOUT`
8. `HIGH_LOAD`
9. `SERVICE_UNAVAILABLE`

The reserved unknown families are intermittent downstream connection failure
and latency jitter / partial degradation. They were excluded from fitting and
threshold tuning.

## Command Center pages

| Page | Connected source |
| --- | --- |
| Overview | Gateway telemetry, incident feed and Candidate 04 readout |
| Live demonstration | Authenticated backend sessions and explicit workflow actions |
| Investigation | Selected persisted case, first observed failure and saved explanation |
| Case evidence | PostgreSQL transfer timeline, citations and financial records |
| Intelligence | Frozen receipts and isolated known-fault acceptance evidence |
| Evaluation | Verified V2 dataset, feature contract, trained families and unknowns |

## Evidence boundaries

- **Operational truth:** persisted transaction, account, ledger and service
  events establish workflow and funds state.
- **Fault localization:** the first saved failed step identifies where the
  workflow first recorded failure. A dependency root cause remains unconfirmed
  unless separately proven.
- **Candidate 04:** classifies an isolated 60-second telemetry window. It does
  not identify a banking transfer or execute recovery.
- **AI explanation:** summarizes saved evidence, labels inference and performs
  no financial or infrastructure action.
- **Recovery:** requires an engineer decision and resumes the same transfer and
  idempotency identity.

## Local endpoints

| Component | Address |
| --- | --- |
| Original gateway | `http://127.0.0.1:18110` |
| Isolated V2 gateway | `http://127.0.0.1:28110` |
| Candidate 04 shadow API | `http://127.0.0.1:28190` |
| Command Center preview | Printed by the installer, normally `http://127.0.0.1:61641/` |

## Acceptance interpretation

The interface reports isolated live acceptance separately for each trained
family. `PASS`, `MISMATCH` and `NOT_RUN` are retained exactly. `NOT_RUN` is
never shown as successful. A live acceptance report must reference the exact
immutable Candidate 04 receipt by SHA256 and retain observed effect, correlated
service evidence and recovery checks as separate facts.

The currently saved payment `SERVICE_UNAVAILABLE` report is a bounded isolated
lab result. It does not prove the root cause of an unrelated persisted transfer
and does not promote the run into training data.

## Safety properties

- No automatic fault activation from the browser.
- No automatic recovery.
- No model retraining or threshold changes from the Command Center.
- No ground-truth label enters the inference feature vector.
- No original PostgreSQL reset.
- Historical receipts never become current health claims.
- Stale or incomplete telemetry clears active claims.

## Local sign-in

- Username: `engineer`
- Password: `faultweave-engineer`

These credentials are for the local Command Center bridge. Do not commit new
production secrets or browser tokens to Git.
