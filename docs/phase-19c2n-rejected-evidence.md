# FaultWeave Phase 19C.2N — Failed resource-effect gate diagnostics

## Purpose

The original resource diagnostic combines two independent acceptance limits into one `STOP` message:

* at least 10 physical PostgreSQL samples inside the exact 60-second window, and
* at least 10 distinct failed client requests with run/trace-correlated service errors.

It also rejects before persisting any evidence. In the previously failed `resource-lock-payment-001` attempt, PostgreSQL subsequently had no waiting/held advisory locks and the seven services were healthy; the previous physical snapshots are not recoverable from the collector. The attempt journal and exclusive supervisor lock remain deliberately preserved.

## Change

Only `scripts/phase19c2n_resource_pilot.py` is patched. The two acceptance limits and all existing fault-activation, timeout, health, rollback and cleanup checks remain unchanged. A rejected effect-gate message now prints exact counts of physical samples, correlated failures, failed client requests and full-window observations.

After lease release, successful recovery checks and feature generation, an effect-gate rejection also persists raw **allowlisted** observations under `data/datasets/v2/rejected-resource/v2resource-<uuid>/`. The directory is create-only, with immutable checksum inventory. It contains `requests.jsonl`, `recovery_requests.jsonl`, `events.jsonl`, `database_samples.jsonl`, and `manifest.json`. It deliberately contains **no feature vector, no known-fault label, no training candidate and no split admission**. Its manifest explicitly says `REJECTED_RESOURCE_EFFECT_GATE`, `training_eligible=false` and `threshold_tuning_eligible=false`.

This patch does **not** retroactively recreate missing database snapshots for the previous failed attempt. It **does not** clear the preserved journal, attempt lock or any fault lease; it does not authorize a repeated experiment.

## Verification

Run:

```powershell
python -m pytest -q tests/test_phase19c2n_failed_evidence.py tests/test_phase19c2n_resource.py tests/test_phase19c2m_service_effect.py tests/test_phase19c2k_collection_controller.py

git diff --check

python -m scripts.phase19c2k_collection_controller --status
```

Expected: new tests pass, the previously failed attempt remains incomplete, and the supervisor lock remains unresolved. Do not run a new resource pilot until the historical failure is reviewed and the lock is reconciled under a separate audited procedure.
