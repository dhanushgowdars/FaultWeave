# FaultWeave Phase 19C.2M — unified bounded service-effect diagnostics

## Purpose and scope

This implementation **reuses** the already-existing `DATABASE_HIGH_LATENCY` database request hook and `SERVICE_UNAVAILABLE` run-scoped HTTP 503 middleware hook. It reuses the existing 19C.2L authentication-rejection pilot and adds one collector, one independent auditor and one small extension to the Phase 19C.2K supervisor. No Docker stopping, Postgres manipulation, DDL, model training, or changes to the existing 240-run proposal.

The bounded service-effect profiles are diagnostic-only:

| Fault | Permitted targets | Intensity | RPS | Note |
| --- | --- | --- | --- | --- |
| `DATABASE_HIGH_LATENCY` | payment, transaction, ledger | medium (750ms configured) | 1.5 | Target service must show real correlated client and service latency |
| `SERVICE_UNAVAILABLE` | payment, ledger | medium | 1.5 | Run-scoped 503 middleware; NOT Docker service stop |

Other mechanisms are **already implemented in the backend** but deliberately **not live-enabled by this adapter**: pool exhaustion and lock contention have lingering DB-connection/lock recovery risks; database unavailable stops the shared Postgres service; high load and full-rate authentication burst lack sufficient calibrated capacity/abort evidence. These must be separately admitted without compromising safety. Low-rate auth rejection was already verified in 19C.2L and is now runnable through the shared controller, without increasing its 1.5 RPS ceiling.

## Before running anything live

1. Install using the guarded installer `--check` then `--apply`. It checks exact source hashes and refuses changed/existing destinations. It backs up the original K controller under `.runtime/phase19c2m/`.
2. Run `python -m pytest -q tests/test_phase19c2m_service_effect.py tests/test_phase19c2k_collection_controller.py` (one existing K fixture test requires the parent plan; expected to pass in the actual repository).
3. Run `git diff --check` and `python -m scripts.phase19c2k_collection_controller --status`; plan remains 240/240 blocked, zero training eligible.
4. Dry-run exactly once without side effects:

```powershell
python -m scripts.phase19c2k_collection_controller --pilot --kind database-latency --target payment --rps 1.5 --dry-run
```

## Opt-in one-pilot execution (only after the tests and preflight pass)

```powershell
python -m scripts.phase19c2k_collection_controller --pilot --kind database-latency --target payment --rps 1.5 --execute-pilot --attempt-id effect-db-payment-001 --acknowledge-diagnostic-only
```

The K supervisor owns a create-only attempt journal and exclusive lock. It invokes the collector in a child Python process, verifies clean stack before/after, identifies exactly one new `v2effect-*` folder and independently audits it. If **anything fails**, it retains the lock/journal for investigation. Do **not** rerun until the incomplete attempt and the active fault state are checked.

After the first successful audited DB latency pilot, a second possible dry-run is:

```powershell
python -m scripts.phase19c2k_collection_controller --pilot --kind service-unavailable --target payment --rps 1.5 --dry-run
```

Do not execute multiple scenarios concurrently or unattended.

## Evidence requirements

Each live pilot schedules 75 seconds at 1.5 RPS, creates a genuine exact 60-second feature window, then sends eight post-clear valid research requests. The independent auditor rechecks:

- Five artifact hashes and sizes, exact run provenance and run/trace ID correlation.
- Feature schema and all 65 features against independently recalculated raw telemetry.
- Truth remains in `protected_ground_truth.json`, absent from the model feature vector.
- For database latency: >=60 in-window requests, >=50% with **both** correlated target-service and client latency >=600ms.
- For middleware availability: >=60 in-window requests, >=50% with **both** correlated target service 503 and propagated client errors.
- Eight post-clear same-run successful recovery requests and pre/post healthy stack.
- `training_eligible=false`, `threshold_tuning_eligible=false`, `split=diagnostic_only` for all results.

These results establish only controlled laboratory mechanism/evidence validity; they do not establish general model accuracy, class-wide target/intensity approval, dataset training eligibility or an unknown holdout.

## Preserved invariants

- Model v1, V1 datasets, source faults, sealed unknown faults, Git commits, and Compose configurations are untouched.
- All planned 240 runs remain blocked with `target_rps=null`; no automatic collection or training.
- The existing 19C.2K diagnostic adapters and attempt locks are preserved; the controller extension adds two service-effect profiles plus the already-verified authentication-rejection profile.
- No rate above 1.5 RPS can be submitted through the new adapter.
- Collectors do **not** call infrastructure stop/start adapters or activate global service-stop faults.
