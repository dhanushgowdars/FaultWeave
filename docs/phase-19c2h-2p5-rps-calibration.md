# FaultWeave Phase 19C.2H — Fixed 2.5 RPS calibration trial

## Why this separate tool exists

The verified Phase 19C.2C healthy collector deliberately caps scheduled traffic at 2.0 RPS. This tool does **not** change or bypass that script's public limits. It is a separately versioned, create-only collector with its own explicit bound: **exactly 2.5 RPS, for one manually initiated 75-second diagnostic run**.

Previous 1.5 and 2.0 RPS healthy trials passed independent artifact audits, but these prove only successful operation at those settings. They do not approve a general safe rate envelope or the 240-run v2 draft.

## Scope and prerequisites

- Runs only the research endpoint `POST /api/v1/transactions` on `http://127.0.0.1:18110`.
- Requires seven healthy Docker Compose services and absence of `data/fault-control/active_fault.json` **before and after** collection.
- Schedules 188 valid requests over 75 seconds at 2.5 requests/second, with a concurrency ceiling of 16 in `experiments.runner.execute_plan`.
- Extracts exactly 60 seconds of feature telemetry and requires all 65 features with strict provenance and timestamp checks.
- Requires all client requests to return valid HTTP 200 results and all requests to have matching trace-correlated service events.
- Saves immutable, create-only artifacts under `data/datasets/v2/calibration/v2cal-<id>`.
- Every artifact stays `diagnostic_only`, `training_eligible=false`, `threshold_tuning_eligible=false`. No run is added to or approved in the blocked 240-run plan.
- Never calls the banking transfer API, fault injection, training, model inference, a high-load simulator, or Git.
- Makes synthetic research transactions in local PostgreSQL when `--execute` is explicitly used; do not run concurrently with other experiments.

## Installation and checks

```powershell
$installer = "$env:USERPROFILE\Downloads\faultweave-phase19c2h-guarded-installer.py"
python $installer --check
python $installer --apply
python -m pytest -q tests/test_phase19c2h_rate_calibration.py tests/test_phase19c2h_integrity_audit.py
python -m pytest -q
git diff --check
python scripts/phase19c2h_rate_calibration.py --dry-run
```

## One manually approved diagnostic trial

Only if the preceding checks pass and no other experiment is active:

```powershell
python scripts/phase19c2h_rate_calibration.py --execute
```

The collector prints `RUN ID: v2cal-...`. Audit using the *actual* run ID:

```powershell
python scripts/phase19c2h_integrity_audit.py --project-root . --calibration-dir "data/datasets/v2/calibration/v2cal-PASTE_REAL_ID"
```

**Note:** The actual CLI flag is `--calibration-dir`, not `--pilot-dir`. The audit automatically adds the chosen project root to Python's import path.

## Acceptance interpretation

`VALID_DIAGNOSTIC_RATE_CALIBRATION` requires artifact hashes, run/trace links, client HTTP success, rate metadata, exact 60-second boundaries and all 65 independently reproduced feature values. The auditor also prints observed in-window client latency p95.

A passing result **does not approve** 2.5 RPS for the 240-run training plan. Compare p50/p95 client and event latency, service-event completeness and system recovery against 1.5 and 2.0 RPS before choosing a conservative rate envelope.

If any stage fails, do not repeat the trial blindly. Confirm service health and no active fault and preserve the failure evidence. No source edits, threshold changes, dataset bulk runs, model retraining, or commit should be performed by this phase.
