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

Checkpoint 4B3 adds the two extended known injectors. Checkpoint 4C builds sealed unknown
scenarios in an isolated evaluation-only path.

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

## Checkpoint 4B2: traffic and pool probes

Authentication bursts generate concentrated invalid-login requests far beyond the
normal-error profile. High load is derived from the local Phase 3 calibration and is
always strictly greater than the selected healthy boundary. Connection-pool exhaustion
holds the selected service's real SQLAlchemy pool capacity during probe traffic and
releases every acquired connection through timed cleanup.

Probe request bodies and database credentials are never written to artifacts. Only
request outcomes, timing, aggregate pressure counts and protected fault metadata are
retained.

```text
python -m experiments.faults.traffic_probe --fault AUTHENTICATION_FAILURE_BURST \
  --target authentication --intensity medium --duration 5
python -m experiments.faults.traffic_probe --fault HIGH_LOAD \
  --target gateway --intensity mild --duration 5
python -m experiments.faults.traffic_probe --fault CONNECTION_POOL_EXHAUSTION \
  --target transaction --intensity high --duration 5
```

## Checkpoint 4B3: extended known faults

Database lock contention uses a real PostgreSQL advisory lock held by a separate
connection. Requests from the exact experiment run wait for the same transaction-scoped
lock, producing lock-wait latency before timed release. Downstream error burst keeps the
selected service alive but deterministically returns controlled 500 responses for a
configured fraction of experiment requests. Health endpoints are never faulted.

```text
python -m experiments.faults.probe --fault DATABASE_LOCK_CONTENTION \
  --target transaction --intensity high --duration 5
python -m experiments.faults.probe --fault DOWNSTREAM_ERROR_BURST \
  --target payment --intensity medium --duration 5
```

## Checkpoint 4C: sealed unknown evaluation harness

The two unseen scenario families are implemented under `experiments/sealed_unknowns`,
outside the known-fault registry and known-fault probe commands. They use the same global
exclusive lease and automatic recovery gate, so known and unseen activations cannot
overlap. Their protected manifests are written only to
`data/experiments/sealed-unknown-evaluation/` and explicitly declare that they are
ineligible for training and threshold tuning.

`INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE` produces deterministic intermittent
connection failures on one dependency edge. `LATENCY_JITTER_PARTIAL_DEGRADATION` creates
a multi-modal mixture of unaffected, moderately delayed and heavily delayed requests;
this is distinct from the fixed-delay known database-latency class. Health endpoints are
excluded and normal traffic is restored after every bounded probe.

These commands are evaluation proof only. Do not run them while generating healthy or
known-fault training datasets.

```text
python -m experiments.sealed_unknowns.probe \
  --scenario INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE \
  --target "transaction->payment" --intensity medium --duration 8
python -m experiments.sealed_unknowns.probe \
  --scenario LATENCY_JITTER_PARTIAL_DEGRADATION \
  --target transaction --intensity high --duration 8
```

## Verification

```text
python scripts/verify_phase4.py
python -m pytest
python -m ruff check .
```

Passing this checkpoint does not claim that the nine injectors are implemented. It
freezes the safety and classification contract they must follow.
