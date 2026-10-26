# FaultWeave Phase 19C.2G — Reachability Correction Proposal (BLOCKED)

## Purpose

The read-only 19C.2F audit of the independently verified 19C.2C healthy research pilot found **10 of 240 planned v2 runs** using `DOWNSTREAM_TIMEOUT` edges which the research workload did not traverse. They were assigned to `payment->account` or `ledger->account`. The audit intentionally returned `NO_GO` (exit code 2). It did **not** authorize fault experiments, traffic rates, or training.

The observed, correlated research-path edges include `transaction->payment` and `payment->ledger`, each exercised by 150 distinct research requests in the healthy reference. These two edges are already allowed targets for `DOWNSTREAM_TIMEOUT` in the fault catalog. Their presence proves **path reachability only**; it does *not* prove controlled timeout exposure or model performance.

## Corrective action

Generate a **new create-only, versioned, blocked proposal** rather than overwriting `phase19c2e-draft-matrix.json`:

`data/datasets/v2/plan/phase19c2g-reachability-proposal.json`

The proposed reconciliation:

- Verifies the parent draft canonical SHA256 `9eed555e72e378a8c7e7d6dc3e3d3724a716fce3b96dafc72d841fd0403567b5` and all its run-level deny flags.
- Revalidates the independent healthy pilot's artifact SHA256 hashes and request/trace correlations by reusing the 19C.2F audit loader.
- Requires a minimum of 10 **distinct correlated requests** on both replacement edges before considering them reachable.
- Changes exactly 10 `DOWNSTREAM_TIMEOUT` **target fields** from `payment->account` or `ledger->account` to `transaction->payment` or `payment->ledger`, distributed evenly across each existing run split.
- Preserves all 240 run IDs, all nine classes, all split assignments (144/48/48), original intensities, seeds, rate regime groups, timestamps/window parameters, and existing gates.
- Records each original and proposed target, source matrix hash, source healthy-pilot run ID, and a new proposal checksum.
- Does **not** alter the original draft or any diagnostic data and refuses to overwrite an existing proposal.

| Split | `transaction->payment` | `payment->ledger` | Total timeout runs |
|---|---:|---:|---:|
| Train | 6 | 6 | 12 |
| Validation | 2 | 2 | 4 |
| Test | 2 | 2 | 4 |

## Execution and validation

After the guarded create-only installer passes `--check`, run its `--apply` action, unit tests, full suite and `git diff --check`.

Run `python scripts/phase19c2g_reconcile.py --dry-run` to validate the exact change list without writing anything. If it passes, run `python scripts/phase19c2g_reconcile.py --write` **once**, then `python scripts/phase19c2g_reconcile.py --verify`. Never use `--write` to overwrite. An existing-file stop is intentional.

`read_draft` and `assess_matrix` from 19C.2F are reused for independent reachability checks. The source 19C.2E draft SHA and actual saved healthy-pilot artifact checksums are validated each time. A changed source or inadequate reference must result in `STOP`.

## Hard stops for future work

The resulting output remains `DRAFT_BLOCKED_NOT_APPROVED`, with `execution_allowed=false`, `training_eligible=false`, `threshold_tuning_eligible=false`, and `target_rps=null` for every run. All proposal-level approvals remain false. It is never passed to any legacy executor.

Path reachability does not establish a safe rate envelope or demonstrate effect/recovery for the timeouts. The next work is **low-risk, healthy-only, hardware-specific rate calibration**, then one bounded diagnostic timeout pilot per proposed target with independent evidence and post-clear recovery. Infrastructure outages, pool exhaustion, lock contention, high load and authentication bursts require separate safety gates, not an implicit mass-run approval. Sealed-unknown data from v1 are not a fresh v2 holdout. Model v1 and all v1 artifacts stay frozen.
