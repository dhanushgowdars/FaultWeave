# Phase 19C.2D — Single known-fault diagnostic pilot

## Scope

One manual, opt-in, 75-second fault-phase workload at 1.2–2.0 RPS on the **localhost research transaction API**. This deliberately injects `DOWNSTREAM_ERROR_BURST` at `payment` with `medium` intensity (bounded deterministic error response). The existing shared `data/fault-control/active_fault.json` lease is exclusive and scoped to the outgoing `X-Run-ID`. No service is stopped, no external API is contacted, no transfer workflow is initiated, no model is trained or altered.

The output is a **diagnostic-only candidate**, not a valid training split and not final model validation.

## Prerequisites and safety

- Seven Docker services healthy; no active/stale fault control file; no other experiments or financial demonstration running.
- Requires the installed Phase 19C.2B exact-window contract and Phase 19C.2C healthy pilot helper.
- The controlled fault lease is at most 105 seconds and is **always released in a `finally` block** on normal script failures/interrupts. Forced OS-level termination cannot run Python cleanup; expiry bounds the lease. Do not manually delete a different experiment's control file.
- An error does **not** produce an accepted candidate. Inspect the fault-control state and Docker health before retrying. Do not run repeated pilots to obtain a desired result.
- Only 1.2–2.0 RPS, fixed 75-second workload; 8 same-run recovery probes after release.

## Evidence and acceptance

1. Confirm the fault lease belongs to the new run ID.
2. Obtain the original correlated research client/service records; require raw trace matching.
3. Select an exact 60-second window fully bounded by start/end, activation/expiration/cleanup, and telemetry watermark, with 2-second margins.
4. Require at least 60 in-window client requests, at least 10 genuine client failures and 5 correlated payment-service HTTP 500 events.
5. Recompute the original ordered 65-feature vector on only observable records, never including protected fault truth.
6. Remove fault lease; require 8 successful **same-run** client requests after cleanup and healthy stack state.
7. Save allowlisted observations, protected ground truth, feature vector, and per-artifact SHA256 under a new, create-only `data/datasets/v2/pilot-fault/v2fault-<id>` directory.

Activation and cleanup timestamps are collected after-the-fact in the **protected** artifact. They are NOT included in model features. Logs omit arbitrary messages, bearer tokens and the protected fault label. This does not establish representative normal traffic, continuous Docker health or readiness to train Model v2.

## Commands

```powershell
python scripts/phase19c2d_fault_pilot.py --dry-run
python -m pytest -q tests/test_phase19c2d_fault_pilot.py
python -m pytest -q
# Only after passing preflight and with no other active experiments:
python scripts/phase19c2d_fault_pilot.py --execute --rps 2.0
```
