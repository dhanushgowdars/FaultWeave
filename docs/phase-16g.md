# Phase 16G — grounded AI explanation quality acceptance

Phase 16G evaluates what the real Groq assistant says, not only whether the API
responds. It runs seven independently generated explanations:

- one completed normal transfer;
- each of the five controlled transfer failures while awaiting engineer review;
- one recovered ledger-unavailable case after explicit engineer review.

The five protected fault definitions remain evaluation-only. Groq receives only
the sanitized evidence contract, current workflow and funds state, ordered event
timeline, case history and optional frozen-model assessment.

## Resumable provider acceptance

The live Groq quality gate runs one case per process. Each result is written to a
case-and-lifecycle checkpoint. A successful case is therefore preserved when a later
provider request is rejected, rate-limited or returns invalid structured JSON. Only the
failed case is retried after the provider quota window resets.

Examples:

```text
python -m scripts.verify_phase16g --case normal-transfer
python -m scripts.verify_phase16g --case account-response-timeout
python -m scripts.verify_phase16g --aggregate
```

Aggregation reads the seven saved checkpoints and makes no hosted-AI request.
An earlier multi-case acceptance JSON can be reused without repeating its successful
provider calls:

```text
python -m scripts.verify_phase16g --import-report data/experiments/phase16g-ai-quality/acceptance-TIMESTAMP.json
```

Only cases that already passed the complete real-Groq quality contract are imported.

## Objective checks

Every generated response must:

- use the configured Groq GPT-OSS 120B provider and `AI_GENERATED` mode;
- cite the exact first persisted failed event for a fault case;
- name the service and workflow step recorded by that event;
- distinguish the timestamp-ordered downstream fault origin from the later
  transaction-level propagation event;
- describe the authoritative funds and workflow states without contradiction;
- treat `UNKNOWN` funds as unresolved: never convert uncertainty into a claim
  that debit and credit definitely succeeded or definitely failed;
- contain at least three cited observed facts, three investigation checks and
  two limitations;
- cite only event IDs from the selected case;
- preserve workflow/journal authority and protected-ground-truth isolation;
- claim no operational action and expose no account number or idempotency key;
- distinguish awaiting-review, normal-completed and recovered lifecycles.

Service event sequence numbers are local, not global. The acceptance contract
therefore orders cross-service evidence by timestamp and uses each service's
sequence only as a tie-breaker. Protected expected fault steps are compared with
the observable operational-origin history, not with the transaction coordinator's
later `payment_execution` stop event.

Judge-facing prose follows a fixed story: customer outcome, first persisted
failure, operational origin, funds safety, and engineer action or verified
recovery. Raw event identifiers remain in citation arrays and the evidence view;
they are not placed inside explanatory sentences.

The live verifier also applies the configured provider delay before the recovered
case, not only between fault families. This allows strict seven-case acceptance
to run against a token-limited provider without bursting two large explanations
back-to-back.

The live verifier always resumes every started fault case after inspection. It
saves the complete sanitized AI responses and per-check results as JSON, plus a
readable Markdown report suitable for human review. A passing report demonstrates
bounded explanation quality on these controlled cases; it does not establish
universal LLM correctness.
