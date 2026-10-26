# FaultWeave — remaining two known-fault families

**Status:** Offline-only implementation, **not live-verified**; latest user-reported
admission total is **102/126**. All existing captures, models, Docker volumes,
original 240-run blocked proposal, and source files must remain unchanged.

## Install

Use the matching guarded installer and source ZIP from the same download set.
From the **FaultWeave project root**, with its Python virtualenv activated:

```powershell
$installer = "$env:USERPROFILE\Downloads\faultweave-v2-final-two-safe-installer.py"
python $installer --check
python $installer --apply
python -m pytest -q tests/test_faultweave_v2_final_two.py
```

The installer refuses to overwrite any existing destination; no Docker or
network operations are run by installation. If `--check` stops, don't force it.

## Priority A — High load: calibrate before reserving any training slot

Check the existing seven-container stack is healthy and that no experiment runs:

```powershell
docker compose ps
python -m scripts.phase19c2k_collection_controller --status
python -m scripts.faultweave_v2_final_two_capture --preview --label HIGH_LOAD --rps 12 --start-slot 1 --count 1
```

If healthy and supervisor unlocked, run **one diagnostic-only calibration**:

```powershell
python -m scripts.faultweave_v2_final_two_capture --calibrate-high-load --rps 12 --acknowledge-fresh-research-traffic
```

- Workload is 75s at **12 RPS**: about 900 actual new, locally generated transactions.
- Calibrate with no other workload, frontend polling, or fault injection.
- It requires verified client impact **and** evidence of gateway-side slowdown
  (or correlated server failures). A merely high request rate is rejected.
- A rejected calibration changes **no training slots**, and should not be relabeled.
- Other bounded options: **8, 10, 16 RPS**; do not automatically increase load.
- Retain output and discuss the next step. 16 RPS may stress a local computer.

Only *after one live rate is verified* and after checking all seven services healthy,
one training sample can be attempted with explicit acknowledgement:

```powershell
python -m scripts.faultweave_v2_final_two_capture --batch --label HIGH_LOAD --rps 12 --start-slot 1 --count 1 --acknowledge-fresh-research-traffic
```

This example assumes 12 RPS calibration passed; use the actual independently
verified rate. Do not run 8 slots before slot 1 passes.

## Priority B — Isolated PostgreSQL, never the existing database

**Requires enough RAM/CPU for seven extra containers.** Execute only after
finishing the high-load experiment and checking memory capacity.

The isolated fixture has project `faultweave-v2-isodb`, its own Docker PostgreSQL
volume (`faultweave-v2-isodb_faultweave_postgres`), its own fault-control and
demo-orchestration directories, and ports 28110–28115 / 25434. It is designed
never to stop original `faultweave-postgres-1` or touch its volume.

Preview and create the **configuration** (creates source fixture files, not Docker):

```powershell
python -m scripts.faultweave_v2_isolated_db --preview
python -m scripts.faultweave_v2_isolated_db --prepare
```

Validate/launch separate stack with explicit acknowledgment:

```powershell
python -m scripts.faultweave_v2_isolated_db --start --acknowledge-separate-seven-container-stack
python -m scripts.faultweave_v2_isolated_db --verify
python -m scripts.faultweave_v2_isolated_db --smoke
```

`--smoke` makes one isolated test transaction and is required to prove the
isolated database contains a working demo account before collecting an outage.
If clone startup, DB URL inspection, memory capacity, health or smoke fails,
**STOP**. It is *not safe* to switch to the primary PostgreSQL container.

Preview a DB-unavailable slot:

```powershell
python -m scripts.faultweave_v2_final_two_capture --preview --label DATABASE_UNAVAILABLE --rps 1.5 --start-slot 1 --count 1
```

Only after the isolated stack has been verified and smoke-tested:

```powershell
python -m scripts.faultweave_v2_final_two_capture --batch --label DATABASE_UNAVAILABLE --rps 1.5 --start-slot 1 --count 1 --acknowledge-fresh-research-traffic --acknowledge-isolated-db-stop
```

That command targets **only** `faultweave-v2-isodb`'s postgres container for a
bounded outage. It attempts restoration in a `finally` block and must prove
both isolated outage and original Postgres health, followed by recovery.
**Do not** issue `docker compose down -v` or delete either database volume.

## Independent audit and admission

A successful `--batch` calls **separate** audit/check and admission subprocesses.
Each expected record uses fresh request/trace IDs, an exact 60s feature window,
65 observables, manifest SHA256s, and an immutable intent recorded before
traffic. Slots 1–8 are training; 9–10 validation; 11–12 test.

Auditor commands for one captured slot (read-only then create-only):

```powershell
python -m scripts.faultweave_v2_final_two_audit --check --label HIGH_LOAD --slot 1
# Automatic --admit occurs only after --check succeeds. Don't manually admit a failed run.
```

The database audit recalculates impacts from immutable observations and checks
consistency of a *capture-time physical Docker stop witness*. It **cannot
independently replay** a historical Docker stop after the container is back up;
do not equate a saved witness with external hardware attestation.

## Important research limitations

- The initial 126 runs provide only 84 training rows, including 8 per known fault.
  They're a modest academic dataset; accuracy/generalization is unknown.
- High-load examples can be recognized by raw request rate rather than failure
  cause. Test with different rates and workloads before strong claims.
- DB outage examples come from a different isolated stack, which may introduce
  domain shift relative to the main stack; evaluate this explicitly.
- Repeated executions at the same settings are independent captures but not
  automatically varied stress conditions.
- Hold-out split rows must never be trained upon; keep sealed unknown fault IDs
  outside known-class training. Do not promote historical diagnostic pilots.
- These scripts are **unverified on your local Docker**; proceed with a single
  calibration and send the output before committing to 12+ new runs.
