# Phase 19B.1 — Frozen V1 compatibility audit (read-only)

This additive patch creates **only** `scripts/audit_phase19b1.py`, `tests/test_phase19b1_audit.py` and this document. It never deletes, restores, stashes, rewrites, or retrains the Phase 19B working tree. The script uses metadata JSON, not model pickles; it does not change frozen v1 artifacts.

## Preconditions

From the FaultWeave repository root, record:

```powershell
git status -sb
git log -6 --oneline --decorate
git diff --check
```

Do not merge or commit unfinished Phase 19B. Do not replace the Phase 19B acceptance report.

Before applying, from the repo root:

```powershell
$paths = @('scripts/audit_phase19b1.py', 'tests/test_phase19b1_audit.py', 'docs/phase-19b1-audit.md')
$existing = @($paths | Where-Object { Test-Path $_ })
if ($existing.Count -gt 0) { throw "STOP: patch path already exists: $($existing -join ', ')" }
git apply --check "$env:USERPROFILE\Downloads\faultweave-phase19b1-guarded.patch"
# Only after preflight passes:
git apply "$env:USERPROFILE\Downloads\faultweave-phase19b1-guarded.patch"
```

Use the actual downloaded patch location if different. `git apply` does not stage/commit anything.

## Exact artifacts used

- `data/datasets/final/models/rich-v2-60s/{isolation-forest,xgboost-known-fault,class-conditional-open-set}/metadata.json`
- `data/datasets/final/features-rich-v2/windows-60s.jsonl` (original train split, for training min/mean/max)
- A **saved, observable-only, pre-inference 60-second snapshot** with all 65 numeric features and metadata. The Phase 19B summary alone is insufficient; if `acceptance-20261011T053016Z.json` does not contain a snapshot, stop. Do not fabricate one or rerun a challenge to match a desired score.

Accepted snapshot JSON:

```json
{
  "window_seconds": 60,
  "coverage_seconds": 65.680836,
  "request_observation_source": "client_observed",
  "request_count": 178,
  "event_count": 3824,
  "features": {"...": "all 65 actual numeric keys; do not use placeholder values"}
}
```

Do not export scenario IDs, hidden class, nonce, approval tokens, credentials, or financial details to the snapshot. If the original feature vector was not persisted, add *a separately reviewed, new-file-only observable snapshot export* to the **next** capture harness; it cannot be reliably reconstructed from summary scores alone.

## Audit command

```powershell
python -m pytest -q tests/test_phase19b1_audit.py
python scripts/audit_phase19b1.py `
  --snapshot "<EXISTING-PATH-TO-SAVED-OBSERVABLE-SNAPSHOT>.json" `
  --model-root data/datasets/final/models `
  --training-rows data/datasets/final/features-rich-v2/windows-60s.jsonl `
  --candidate DATABASE_HIGH_LATENCY `
  --expected-score 4916.66666667 `
  --expected-limit 10.8173424 `
  --report data/experiments/phase19b1-audit/phase19b1-initial-audit.json
```

Optional (only if you saved raw observable evidence captured for that same window): add `--requests-jsonl <path>` and `--events-jsonl <path>` together. The script recomputes the two feature aggregations, compares them to the 65 saved live values, and reports the number of excluded events. If the raw files are missing, source equivalence stays **UNVERIFIED**.

The report path must not exist. Python's exclusive-create mode will refuse to overwrite it. A PASS is only a **numeric reproduction of the support score**; it is not a PASS for the entire compatibility audit.

## Decision gate

1. Verify matching names, ordering, types, units, 65-element scales/centers, and score. Exact score reproduction identifies the maximizing feature(s) by `responsible_features`.
2. Review fitting-training distribution (`training.minimum`, `training.median`, `training.mean`, `training.maximum`) and each normalized distance. Compare the same named unit.
3. Check raw request/event provenance and filtering manually. The training feature extractor bucketed records by timestamp without `eligible_runtime_event`, while the live ingestion path filters health/intelligence events and may choose `client_observed` or gateway-derived requests. The included aggregation probe tests cover selected cases, not a complete proof of raw input equivalence.
4. Check that source capture included only the intended challenge interval. Training v1 60s rows were built from a pure fault interval; the Phase 19B rolling 60s snapshot overlapped baseline, challenge and recovery. Its challenge was only 30 seconds, so a 60-second fault-only window cannot be recovered from that run. A later fault-phase-only experiment requires at least 60 seconds of genuine coverage before recovery begins. Phase 19B.2 must handle corrected windows without using ground truth.
5. If corrected contract and window prove compatible, keep v1. Otherwise declare versioned v2 data/model work only after this single audited correction cycle. Never tune support thresholds against this revealed case.

## Verification after applying

```powershell
git diff --check
python -m pytest -q tests/test_phase19b1_audit.py
python -m pytest -q
git status -sb
```

This patch intentionally **does not perform a corrected v1 rerun**, alter orchestrator flows, or implement Phase 19B.2 windowing. It reports raw source-aggregation and window-isolation verification as outstanding, rather than claiming they passed without evidence.
