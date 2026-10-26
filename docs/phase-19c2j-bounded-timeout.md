# Phase 19C.2J — Bounded Dependency-Timeout Diagnostic (Not Training Data)

## Purpose

The independent Phase 19C.2I report confirms 240 planned v2 collection runs are **blocked**, with **zero training-eligible** rows. Phase 19C.2J does not change or approve that proposal. It only creates a reusable **single-run, manual, diagnostic** collection mechanism for `DOWNSTREAM_TIMEOUT` using two confirmed research-workload dependencies:

- `transaction->payment`, expected failing downstream call `/internal/v1/payments`
- `payment->ledger`, expected failing downstream call `/internal/v1/ledger/...`

The old `payment->account` and `ledger->account` targets remain excluded because they were absent from the observed research transaction path. This package introduces no infrastructure stops, destructive DB operations, transfers, new model, thresholds, or training eligibility. Other fault families continue to require their own validated mechanics and resource checks.

## Contract and safety

1. `--dry-run` is available without Docker or network. It re-verifies the **fully blocked** corrected matrix using the already installed Phase 19C.2I safety checks.
2. `--execute` is opt-in. Rate must be **exactly 1.5 or 2.0 RPS**, duration 75 s, bounded fault lease 105 s; runs only via `http://127.0.0.1:18110/api/v1/transactions` (the existing simulated research route).
3. It checks all seven Compose services healthy and requires no pre-existing fault lease, before and after. It acquires one run-scoped `DOWNSTREAM_TIMEOUT`, `medium` intensity lease and releases it in `finally`, including cancellation. It never calls infrastructure stop adapters.
4. It requires an exact fault-only 60-second window, a telemetry watermark, 65 correctly ordered features, at least 60 in-window client observations, at least 10 affected clients, and at least 5 correctly correlated timeout events on the configured source→target edge. Counted timeout events must have the expected source, destination, internal path, `ReadTimeout`, run/trace ID, and be inside the window; fake or unrelated errors cannot satisfy the gate.
5. Eight same-run **successful** recovery requests must start **after** fault cleanup, with correlated event evidence.
6. Evidence is create-only under `data/datasets/v2/pilot-fault/v2timeout-...`: original raw requests, recovery requests, structured events, an **unlabeled** 65-feature candidate, separate protected truth, and a checksum manifest. Uses original Phase 19C.2D persistence format with Phase 19C.2J truth type and source metadata. No v1 artifacts or previous pilots are overwritten.
7. The independent read-only verifier recalculates SHA256s, checks client/event provenance, matches the exact window and feature vector, verifies the intended dependency timeout and post-clear recovery, and ensures the run remains diagnostic-only.

## Suggested first trial, after installation/tests

```powershell
python -m scripts.phase19c2j_timeout_pilot --dry-run --target "payment->ledger" --rps 1.5
# Only if this dry run and all unit tests pass, with no other experiment active:
python -m scripts.phase19c2j_timeout_pilot --execute --target "payment->ledger" --rps 1.5
# Replace the run ID below with the actual output, never the placeholder:
python -m scripts.phase19c2j_timeout_audit --project-root . --pilot-dir "data/datasets/v2/pilot-fault/<ACTUAL_RUN_ID>"
```

If the experiment stops, **do not immediately repeat it**. Confirm health and absence of `data/fault-control/active_fault.json`. Retain partial evidence and debug without tampering with the frozen model.

## Limitations and next gates

- One successful diagnostic is **not** per-class approval; no planned v2 row changes `execution_allowed`, `training_eligible`, or `threshold_tuning_eligible`.
- All nine fault families, their intensities and target combinations, healthy-rate representativeness, continuous resource monitoring, and unknown-family sealed holdout remain open research gates.
- This does not establish real-world fault accuracy or open-set performance. Train Model v2 only after the finalized collection rules and independently held-out evaluation plan are approved.
