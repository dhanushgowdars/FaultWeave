# Phase 4 — Controlled Known-Fault Injection

Phase 4 introduces bounded operational faults without weakening the Phase 3 normal-data
contract. Fault injection metadata remains separate from service-event features.

## Checkpoint 4A: safety and registry contract

- Exactly one fault activation may exist at a time.
- Every activation has a run ID, known family, validated target, intensity, start and expiry.
- The maximum activation duration is five minutes.
- Activation state is runtime data and is excluded from Git.
- Cleanup runs even if activation or experiment execution fails.
- Recovery verification runs before the activation lease is released.
- Core and extended known faults are separate from sealed unknown identifiers.

The seven mandatory core families are database high latency, database unavailable,
service unavailable, downstream timeout, authentication failure burst, high load and
connection-pool exhaustion. Database lock contention and downstream error burst are the
two extended known families.

The sealed identifiers are declarations only. Their implementation and parameters must
not enter known-fault generation, model training, feature selection or threshold tuning.

## Remaining Phase 4 checkpoints

Checkpoint 4B implements each core injector and recovery adapter. Each injector is
accepted independently with a short experiment proving activation, observable impact,
bounded duration, cleanup and restored normal flow.

Checkpoint 4C adds the two extended known injectors. Sealed unknown injectors are built
later in an isolated evaluation-only path.

## Checkpoint 4B1: infrastructure probes

The first four injectors now use two mechanisms:

- Database latency and downstream timeout are scoped by exact run ID through the
  read-only fault-control mount. Normal and health-check traffic is unaffected.
- Database and service unavailability stop the selected Compose service and restore it
  through a guaranteed cleanup lease.

Every short probe writes protected experiment metadata separately under
`data/experiments/fault-probes/`, then restores the stack and completes a normal smoke
transaction. Probe artifacts are evidence only and are excluded from Git.

Example bounded probes:

```text
python -m experiments.faults.probe --fault DATABASE_HIGH_LATENCY \
  --target transaction --intensity high --duration 5
python -m experiments.faults.probe --fault DOWNSTREAM_TIMEOUT \
  --target "transaction->payment" --intensity medium --duration 5
python -m experiments.faults.probe --fault SERVICE_UNAVAILABLE \
  --target payment --intensity medium --duration 5
python -m experiments.faults.probe --fault DATABASE_UNAVAILABLE \
  --target postgresql --intensity medium --duration 5
```

The remaining three core mechanisms—authentication burst, load above the calibrated
healthy envelope and real pool exhaustion—are implemented in Checkpoint 4B2.

## Verification

```text
python scripts/verify_phase4.py
python -m pytest
python -m ruff check .
```

Passing this checkpoint does not claim that the nine injectors are implemented. It
freezes the safety and classification contract they must follow.
