# FaultWeave Model v2 — compact candidate-training workflow

This is a **final-year-project-sized** training workflow, not a new production platform. It keeps the existing 240-row proposal, Model v1, frontend, and both sealed unknown mechanisms untouched.

## Implemented now

- Deterministic **126-run blocked subset** of the verified 240-row proposal: 18 normal (12 train, 3 validation, 3 test), 12 for each of the nine known faults (8 train, 2 validation, 2 test).
- Tries to balance existing intensity, rate-regime and target selections while preserving **the original run ID and split** for every selected row. All selected rows remain `execution_allowed=false`, `training_eligible=false`, and `target_rps=null`.
- **Training-data admission check** requiring 126 independently captured exact-60-second rows, one per run, the same 65 numerical features, run-level split isolation, and SHA256-confirmed source manifests and independent audit records that explicitly approve the new run. Diagnostic pilots are rejected.
- **Candidate Model v2 trainer** (only after admission): Isolation Forest normal-only training; XGBoost nine-class known-fault training; anomaly and open-set confidence/distance thresholds fitted on known validation rows only; sealed unknowns excluded from fitting and tuning. Produces separate joblib models, held-out test metrics and confusion matrix under a new folder in `data/datasets/v2/models/`. Does **not** activate or deploy a model.

## What this does not do

- Does not execute any of the blocked 126 or 240 dataset runs.
- Does not convert earlier diagnostic-only captures or historical windows into training data.
- Does not automatically grant training approval, simulate 126 measurements, or manufacture labels.
- Does not train Model v2 until real independent training-run receipts and measured features exist.
- Does not validate fresh sealed unknowns or demonstrate incident localization / dashboard integration. Those are later integration checks.
- Does not authorize PostgreSQL shutdown. Use a separate isolated database for the `DATABASE_UNAVAILABLE` research run.
- 8 RPS did **not** establish overload. A `HIGH_LOAD` known-class label requires a stated and defensible measured workload condition.

## Commands (PowerShell, project root)

```powershell
python -m pytest -q tests/test_faultweave_v2_fasttrack.py
python -m scripts.faultweave_v2_fasttrack --preview
```

Optionally, save the blocked compact draft **once**:

```powershell
python -m scripts.faultweave_v2_fasttrack --write-draft
```

**Future commands only after independently verified new training captures exist** (these are *not yet available*):

```powershell
python -m scripts.faultweave_v2_fasttrack --check-training --features data/datasets/v2/ingest/admitted-features.jsonl --receipts data/datasets/v2/ingest/admissions.json

python -m scripts.faultweave_v2_fasttrack --train-candidate --features data/datasets/v2/ingest/admitted-features.jsonl --receipts data/datasets/v2/ingest/admissions.json --output data/datasets/v2/models/v2-candidate-01 --acknowledge-training-candidate
```

The independent training-run capture/admission workflow **is still to be implemented**. Admission receipts are intentionally incompatible with diagnostic-only manifests. An admission receipt contains `schema_version=faultweave-v2-run-admission.1`, `feature_file_sha256`, `independent_audit_required=true`, and one receipt per fresh run: run ID, label, split, source-manifest and audit SHA256/path, and `training_admission=true`. Both JSON evidence documents must have the same run ID. The source manifest needs explicit `training_eligible=true`, matching split/label, and 60-second windows. The audit must say `verdict=VALID_V2_TRAINING_RUN` and `independent=true`. These local audit documents must be produced by a real independent auditor; file hashes alone cannot prove a collection was honest.

## Selection constraints

Selection is a compact alternative to the original plan, **not an accuracy guarantee**. Because each known fault receives only eight training runs, report the actual measured per-class test results and expand weak classes rather than claiming production-generalizable performance. Keep the sealed unknown evaluation separate and untuned until the candidate is frozen. Only switch the API to Model v2 after test/evaluation success and explicit integration verification. Model v1 stays available for comparison and rollback.
