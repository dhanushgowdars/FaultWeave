# Phase 19A — sealed blind random challenge

Phase 19A adds a judge-facing random execution mode without turning FaultWeave into an
unbounded chaos tool. The server selects from the one normal and five fault scenarios that
already passed Phase 17 financial and recovery acceptance.

## Security and scientific boundaries

- The client cannot submit a scenario identifier in `BLIND_RANDOM` mode.
- The selected scenario, target, intensity and nonce remain server-private through transfer
  execution, model inference and grounded-assistant generation.
- A SHA-256 commit/reveal record proves that the server did not change the selected scenario
  after seeing the diagnostic result.
- Demo ground truth is never supplied to feature extraction, frozen-model inference or the
  assistant prompt.
- Blind execution is one server action. The client cannot separately arm or execute its
  hidden scenario.
- Every injected fault retains the existing bounded lease and is cleared on success or error.
- An investigation must be saved before the scenario can be revealed.
- Recovery remains human-gated and resumes the same idempotent transfer.

## API contract

Prepare a random challenge with `POST /api/v1/demo/sessions` and:

```json
{
  "mode": "BLIND_RANDOM",
  "amount_minor": 1000,
  "currency": "INR"
}
```

The public response exposes `SEALED_RANDOM_CHALLENGE` plus a commitment hash, not the
selected fault. `POST /api/v1/demo/sessions/{id}/run` performs internal preparation and one
real durable transfer. The existing `/analyze` endpoint then creates evidence, model and
assistant views. Only after that succeeds can `/reveal` return the selected scenario, nonce
and comparison.

The comparison distinguishes three facts:

1. the injected scenario revealed by the commit/reveal record;
2. the frozen model class, when a correlated 60-second window is available; and
3. the first failed workflow step observed in persisted transaction evidence.

`WINDOW_NOT_READY`, `WINDOW_NOT_CORRELATED` and `UNAVAILABLE` remain honest model outcomes;
they are not converted into a false match.

## Acceptance

Unit and API tests prove deterministic selection, catalogue coverage, request validation,
pre-reveal redaction, exclusive fault control, cleanup after errors, reveal ordering,
commitment verification and idempotent repeated reveal.

With Docker services running, execute:

```powershell
python -m scripts.verify_phase19a
```

The live verifier runs one server-selected case end to end, blocks direct control and early
reveal, verifies ground-truth exclusion, validates the public SHA-256 commitment and, when a
fault was selected, performs engineer-approved same-transfer recovery.
