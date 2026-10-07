# Phase 15F - Guided incident demonstration

Phase 15F replaces the rejected automatic demonstration with an explicit, judge-facing
incident investigation. It preserves the verified microservices, dataset, frozen models,
localization, severity, live APIs and evidence contracts from Phases 1-14.

## Product statement

FaultWeave observes a distributed transaction, detects abnormal behaviour, explains the
supporting evidence, identifies the probable failure origin and helps an engineer verify
recovery.

## Required demonstration states

1. `READY`: all services are healthy and no controlled fault is active.
2. `FAULT_ACTIVE`: the operator has injected one bounded fault; no transaction has run.
3. `TRANSACTION_RUNNING`: one highlighted customer request and the scenario's
   explicitly disclosed supporting requests are executing. All results have
   separate request and trace identifiers.
4. `AWAITING_REVIEW`: the original outcome, ordered service path, logs and intelligence
   result are frozen for engineer review. The fault remains active.
5. `READY_TO_VERIFY`: an engineer approved remediation and infrastructure health returned.
6. `VERIFYING`: a new request with new request and trace identifiers is executing.
7. `RESOLVED`: the new transaction completed and recovery is proven.

No controller action may combine fault injection, customer traffic, remediation and recovery
verification. Every transition is explicit and persisted.

## Judge-facing workflow

- Select a scenario.
- Inject the controlled fault.
- Confirm which component changed.
- Send the highlighted simulated transaction with the disclosed supporting
  traffic used to produce an observable fault signal.
- Follow Gateway, Authentication, Account, Transaction, Payment and Ledger in order.
- Review the customer outcome, first failed event and exact correlated structured logs.
  Show a frozen-model result only if the live incident API links it to this run.
- Approve the proposed remediation.
- Verify recovery with a different request ID and trace ID.

## Evidence rules

- Architecture state comes from the selected controlled condition and request-correlated
  events, not decorative timers.
- HTTP exchange completion is never described as business success unless the transaction
  result succeeded.
- The controlled cause is labelled as test ground truth and remains separate from the model's
  probable-origin prediction.
- Missing live-model evidence is displayed as unavailable or waiting; it is never fabricated.
- An incident detected in an aggregate live window cannot be attached to a case
  by timestamp alone. The case shows a model conclusion only when the incident
  API explicitly links it to that demo run.
- Service states after the request reflect request-correlated events. The
  selected fault is separately labelled as the controlled test condition.
- Original and recovery requests remain separate.
- A safety watchdog may remove an abandoned fault, but such a case is marked `ABORTED` and is
  never presented as engineer-approved recovery.

## Acceptance boundary

Code checks do not prove the live case end to end. Before describing this phase as
complete, run a real fault with Docker, confirm that the highlighted request has
correlated events, leave it awaiting engineer review, approve remediation, then
confirm the separate recovery request succeeds. The in-process watchdog does not
survive a controller process crash; after an unexpected stop, inspect the fault
lease and Docker health before another demonstration.

## Information architecture

- **Overview**: problem, solution, architecture and verified research results.
- **Demo Lab**: the single guided workflow and its live evidence.
- **Cases**: persistent review queue and resolved-case audit trail.
- **Evaluation**: frozen metrics, dataset scale and honest limitations.
- **System**: service topology, observable features and model boundaries.

## Presentation constraints

- One primary architecture diagram per case.
- One next action at a time.
- No raw JSON on the main demonstration surface.
- No `1/3`, `2/3` or vote-like model language.
- No continuous unexplained log buffer.
- Projector-readable typography and restrained operational colours.
- The default judge scenario is Payment Service Unavailable.
