# FaultWeave Model v2 — five additional verified fresh-training families

## Scope

Adds **new** 75-second, 1.5-RPS captures with strict 60-second windows and immutable per-run training intent. The new collector is separate from the existing NORMAL and `DOWNSTREAM_ERROR_BURST` collector; your already-admitted evidence is not rewritten.

Supported configurations (deliberately narrowed to previously proven diagnostic targets and intensities):

| Class | Target | Intensity | Extra proof |
| --- | --- | --- | --- |
| `DOWNSTREAM_TIMEOUT` | `payment->ledger` | medium | correlated client error and direct source-to-ledger timeout |
| `DATABASE_HIGH_LATENCY` | `payment` | medium | matched client/payment latency >=600ms |
| `SERVICE_UNAVAILABLE` | `payment` | medium | correlated payment 503 and propagated failure |
| `CONNECTION_POOL_EXHAUSTION` | `payment` | high | physical PostgreSQL connection spike, service failures, recovery |
| `DATABASE_LOCK_CONTENTION` | `payment` | medium | physical PostgreSQL advisory-lock waiters, service failures, recovery |

**Still explicitly blocked:** `DATABASE_UNAVAILABLE` (persistent database must not be stopped), `HIGH_LOAD` (8-RPS test did not prove overload), and `AUTHENTICATION_FAILURE_BURST` (the tested 1.5-RPS authentication rejection case does not prove a high-rate burst). No relabeling or synthetic promotion is allowed.

## Basic commands — Windows PowerShell in the FaultWeave repository

First verify a healthy local Docker stack and no unresolved supervisor reservation:

```powershell

docker compose ps
python -m scripts.phase19c2k_collection_controller --status
python -m pytest -q tests/test_faultweave_v2_verified.py
python -m scripts.faultweave_v2_verified_capture --preview --label DOWNSTREAM_TIMEOUT --start-slot 1 --count 1
```

One NEW live timeout capture with **separate-process** independent check and admission:

```powershell
python -m scripts.faultweave_v2_verified_capture --batch --label DOWNSTREAM_TIMEOUT --start-slot 1 --count 1 --acknowledge-fresh-research-traffic
```

**Only if slot 1 succeeds**, proceed with slots 2–6. The command stops at the first error, preserves evidence, and does not skip failed slots:

```powershell
python -m scripts.faultweave_v2_verified_capture --batch --label DOWNSTREAM_TIMEOUT --start-slot 2 --count 5 --acknowledge-fresh-research-traffic
```

For `DATABASE_HIGH_LATENCY` and `SERVICE_UNAVAILABLE`, substitute the `--label` value (each family has distinct slots 1–12). Try one slot per class first, then use 5–10 slot batches. Physical resource classes additionally require explicit `--acknowledge-db-pressure`, complete worker/hash/connection-budget preflight, and must be run only when the demonstration stack is not in active use. Do not start those automatically merely because the installer passes.

## Integrity and evidence

A run stores `intent.json`, `requests.jsonl`, `recovery_requests.jsonl`, `events.jsonl`, `feature_row.json`, `capture_metadata.json`, `protected_ground_truth.json`, `source_manifest.json`, and after successful independent audit, `independent_audit.json` + `admission_receipt.json`. Physical resource faults also store `db_samples.jsonl` (SHA256 protected). The 65-feature vector never receives protected ground truth or physical measurement labels.

One new slot is permanently reserved once intent is created. On an error, the lock and partial evidence remain; never delete/retry blindly. Manual intervention after inspection is required.

This is **offline-test verified, not live tested**. The attached unit tests use mocked telemetry to exercise manifest and auditor plumbing; real live fault gates remain unchanged and must pass independently. The original 240-run plan stays blocked and no existing model or frontend files are changed.

## Scientific limits

Only one previously proven target and rate is admitted per new class here. That creates a potentially narrow training distribution. It can support a prototype, but does not prove cross-service or high-rate generalization. Preserve distinct train (slots 1–8), validation (9–10), and test (11–12) runs. Don't tune thresholds or revise fault configurations using the test set. A fully validated nine-class Model v2 still requires three correctly evidenced families and final model evaluation.
