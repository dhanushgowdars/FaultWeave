# FaultWeave — physical database-resource diagnostics (Phase 19C.2N)

## Scope

Add two diagnostic-only adapters to the previously verified unified collection supervisor:

- `CONNECTION_POOL_EXHAUSTION` on `payment` at **high** intensity, **1.5 RPS**.
- `DATABASE_LOCK_CONTENTION` on `payment` at **medium** intensity, **1.5 RPS**.

Uses existing `FaultStateStore` and the existing 75-second workload / strict 60-second feature window. No PostgreSQL or container stop, no infrastructure-fault adapter. High load, database unavailability, and high-rate authentication remain **blocked**. All 240 planned Model v2 collection rows remain blocked, with **zero training eligibility**.

## Why high intensity for pool diagnostics?

In the reviewed source, each payment worker's SQLAlchemy pool has `pool_size=5`, `max_overflow=10` and a five-second checkout timeout. The `high` pool fault checks out 15 connections, while `medium` checks out 12. The latter may leave three available connections and fail to demonstrate resource exhaustion. This limited diagnostic is only for one payment worker, and the runner stops if the actual deployed worker topology differs from the reviewed configuration.

## Fail-closed preflight

Before fault acquisition the runner rechecks all-service health, absence of an active fault-control file, that the running `payment-service` loads the patched fault framework, single-instance service configuration plus the *running container* command and `WEB_CONCURRENCY` worker setting, PostgreSQL maximum connections, reserved slots, and actual connection usage. It subtracts a worst-case 15 connections and requires **at least 40 additional connections left after reserved slots**. The 100/14 snapshot is an encouraging observation but is **not** a live permission grant. The runner reads the current values each time.

## Physical fault evidence

Every ~2s during the active fault, a read-only PostgreSQL query (using cached local database identity) records connection count and granted/waiting advisory locks. These physical `database_samples.jsonl` observations are kept in a diagnostic-only artifact: **never used in the 65 model features**.

The independent auditor requires:

- Exact 60-second window, 65/65 independently reconstructed features and original run/trace correlation.
- For pool exhaustion: at least eight additional physically observed DB connections vs. the preflight baseline **and** at least ten distinct in-window clients with correlated service failure events.
- For lock contention: increase in PostgreSQL advisory-lock waiters above baseline **and** at least ten distinct clients with correlated service failure events.
- Eight post-clear valid transaction requests and normal stack health.
- Six SHA-256 verified evidence files; the database evidence is not a fault-class shortcut available to the model.

A failure to prove any condition is **not silently interpreted as success**. A failed run preserves the supervisor's attempt journal and unresolved lock for manual inspection. No automatic retries.

The above evidence proves a *diagnostic configuration* only, **not** correct fault classification, class-wide approval, permission to collect Model v2 training splits, or a new unknown holdout.

## Setup and read-only validation

```powershell
$installer = "$env:USERPROFILE\Downloads\faultweave-phase19c2n-resource-guarded-installer.py"
python $installer --check
python $installer --apply
python -m pytest -q tests/test_phase19c2n_resource.py tests/test_phase19c2m_service_effect.py tests/test_phase19c2k_collection_controller.py
git diff --check
python -m scripts.phase19c2k_collection_controller --status
python -m scripts.phase19c2k_collection_controller --pilot --kind connection-pool --target payment --rps 1.5 --dry-run
python -m scripts.phase19c2k_collection_controller --pilot --kind database-lock --target payment --rps 1.5 --dry-run
```

**Do not start live diagnostics until the above local verification has been reviewed.** Live pool exhaustion can temporarily make the payment service unresponsive; the lock pilot can create temporary database waits. Both run only on this isolated local development stack, never a shared or production database.

## Protected files and deployment

The installer refuses unexpected source hashes and refuses destination overwrites. It backs up `scripts/phase19c2k_collection_controller.py` under `.runtime/phase19c2n/` and adds two scripts, one test file and this guide. It does not alter shared fault injection, `.env`, Compose files, running containers, existing datasets, model artifacts, Git history, or the 240-run proposal. The prior resource-lease cleanup patch must already be deployed and match the running payment-service image.
