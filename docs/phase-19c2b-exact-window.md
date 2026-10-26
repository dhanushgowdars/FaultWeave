# FaultWeave Phase 19C.2B — Exact 60-Second Window Contract

**Status:** offline contract smoke only; no dataset-v2 accepted, no live run, no v2 model trained.

## Findings leading to this implementation

- The original v1 raw dataset has 280 well-formed manifests, with 9 known-fault classes, run-level split isolation and no missing referenced artifacts.
- Sampling one raw training run per class found no eligible-event filtering differences. The HIGH_LOAD trace scan was capped and **incomplete**. This result does **not** prove global raw-event parity.
- The original nominal-60s windows often cover other physical durations; v2 MUST use exactly 60 seconds of real observations, not relabel original rows.
- Per-class constant feature counts and load-varying live traffic caused unjustified v1 open-set rejection; v1 stays frozen.

## What is implemented

The create-only module `datasets/phase19c2b_exact_windows.py`:

- Selects exactly 60.000-second `(start,end]` observation windows, aligned to live inference.
- For *fault* windows, requires independent actual phase boundaries, a fault activation time, expiry and recorded cleanup time. Enforces a two-second lead/tail guard, plus an event watermark.
- For *normal* windows, refuses contradictory fault-control proof.
- Fails if a window cannot fit inside the verified fault and phase interval. Never pads, truncates, or merges recovery traffic.
- Builds observable-only candidate 65-feature vectors from **explicitly run-tagged client requests and service events**, matching current training formulas and existing live exclusions.
- Rejects missing or duplicate client request identities; suppresses duplicate structured events like the live buffer; enforces observable temporal coverage at both ends.
- Does **not** accept a fault label, choose a split, train a model, write artifacts or read hidden ground truth.

The unit tests exercise timing, cleanup, watermark, run isolation, exclusion predicates, 65-feature ordering, training/live formula parity, duplicates, and incomplete coverage.

## Limitations and next gate

This is not yet a production v2 collector. The old v1 raw request schema is missing an explicit `run_id` in some observations and MUST NOT be silently upgraded by fabricating provenance. The v2 collector must store explicit `run_id` with every client observation, preserve original request/trace IDs, and record actual fault control/observation timestamps. **Verify all 65 formulas and live eligibility on multiple representative real fault traces**, not just the synthetic tests here.

Before any v2 training:

1. Run these contract tests and the full unchanged regression suite.
2. Design a v2 dry-run collector with explicit run provenance and real `fault_activated_at`, `fault_expires_at`, `fault_cleared_at`, phase boundaries and telemetry watermark.
3. Verify a single isolated normal pilot, then a single fault pilot, and confirm complete 60-second evidence without using hidden labels in features.
4. Set hardware-safe varied-rate regimes, freeze new run-level splits before collection, and write new data only beneath a separate v2 namespace.
5. Keep sealed-unknown v1 evaluation data separate and out of calibration. A fresh unknown family must remain untouched until v2 is frozen.

**Do not run `datasets.final_executor`, retrain Models v1/v2, change thresholds, or rerun randomized blind challenges as part of this contract smoke.**
