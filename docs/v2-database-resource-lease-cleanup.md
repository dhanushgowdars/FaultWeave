# FaultWeave Model v2 — Database resource lease cleanup correction

## What was found

In the uploaded `shared/faultweave_common/fault_injection.py`, `CONNECTION_POOL_EXHAUSTION` and `DATABASE_LOCK_CONTENTION` create background resource-holder tasks that wait until `expires_at`. Clearing the run-scoped global fault lease earlier does **not** release the reserved database connections or advisory lock immediately. Thus the normal 75-second collector can finish and clear a 105-second lease while database resources remain held during post-clear recovery. The original database-lock holder also has an unsynchronized first-request initialization path.

## What this guarded patch changes

- Holders poll their exact fault `activation_id`, `run_id`, `fault_id` and `target` against the active control file, at most every 250 ms.
- Resource holders are released when the lease is **cleared**, **replaced**, or **expired**, with cancellation cleanup retained.
- Lock-holder creation uses an asyncio lock to prevent concurrent first requests from registering independent holders for the same service.
- Holder creation failure closes the acquired connection before propagation.
- Resource registries only remove their own connection/connection set, avoiding deletion of a newer entry.
- No changes to model weights, feature schema, planned training rows, other fault mechanisms, Docker, or container state.

## Safety boundaries

This patch addresses a specific *source-level lease cleanup deficiency*; it does not establish that pool exhaustion or database lock contention is safe on your actual configured PostgreSQL/SQLAlchemy pool. Those families still require bounded pilot design, recovery instrumentation, clean-stack verification and independent artifact audit. In particular, `engine.connect()` may itself block waiting for the pool and requires a separate acquisition timeout/rollback guard before live execution. Do **not** run the legacy high-load, pool, database-unavailable or lock-contention probes just because this patch installs.

Applying this patch updates only local source. If your Docker Compose setup bakes that source into service images, running services will still use the old code until an explicitly controlled rebuild/restart. Do not rebuild running services in the middle of an experiment.

## How to install

From the FaultWeave repository root:

```powershell
$fix = "$env:USERPROFILE\Downloads\faultweave-v2-resource-lease-safety-fix.py"
python $fix --check
python $fix --apply
python -m pytest -q tests/test_faultweave_v2_resource_lease.py
python -m pytest -q tests/test_phase19c2m_service_effect.py tests/test_phase19c2k_collection_controller.py
git diff --check
```

All tests are offline. The installer creates a persistent backup under `.runtime/v2-resource-lease-safety/fault_injection.original.py` and refuses changed/unexpected source hashes or any existing new-file/backup destination.

## Isolated testing performed

In a Python test fixture using fake async database connections and no network, nine lease-holder tests passed (release on clear/replacement/expiry, concurrency, wrong-run no-op and cancellation). With the existing Phase 19C.2K + 19C.2M offline tests, the total was **99 passing**. These are not Docker integration or production fault-tolerance results.
