# FaultWeave Phase 19C.2I — Offline Collection Readiness

## Purpose

Close the exploratory healthy rate calibration using the three saved, independently audited diagnostic references and the one known-fault reference. Validate that the immutable Phase 19C.2G reachability proposal is still exactly the corrected 240-run **blocked** draft. Produce a machine-readable status **without approving experiments**.

## Scope and evidence

- Source draft SHA256: `9eed555e72e378a8c7e7d6dc3e3d3724a716fce3b96dafc72d841fd0403567b5`.
- Reachability proposal SHA256: `577b8e877466aae9df96c161ad911e0278cadaa50cf879dd89110d635c755cd9`.
- Healthy diagnostic runs: 1.5 RPS / 90 in-window requests; 2.0 RPS / 120; 2.5 RPS / 150. All showed 100% request success, although 2.0 RPS had a p99 tail-latency spike. These are **single lab trials**, not approvals.
- Fault diagnostic: run-scoped medium `DOWNSTREAM_ERROR_BURST` on `payment`, 74 of 120 requests impacted, 74 correlated 500 events, 8 successful same-run post-clear controls. This does not generalize automatically to other fault targets/intensities.
- Timeout targets replaced under 19C.2G: `transaction->payment` and `payment->ledger`, based on healthy-path presence only. Real timeout effects must still be demonstrated.
- Nine known fault families each have target/intensity-specific safety and recovery requirements. `DATABASE_UNAVAILABLE` and `SERVICE_UNAVAILABLE` must never be treated as ordinary non-disruptive tests; high-load and authentication-burst families require distinct calibrated rates.

## What this software verifies

1. Re-derives the Phase 19C.2G proposal using the Phase 19C.2G validator and healthy reference, and checks equality with the saved proposal.
2. Validates the 240 blocked rows, 144/48/48 split, nine balanced classes, path correction provenance and absence of executable or training-eligible flags.
3. Checks checksum/size for three saved healthy artifacts per run and five saved fault-pilot artifacts. Checks candidate metadata, 60s timestamps and the recorded feature-vector count. **It does not independently rerun the raw-event feature extractor**; that was the role of the earlier dedicated auditors.
4. Outputs a `NO_GO_BULK_COLLECTION` readiness assessment listing what is still missing. The optional report is separate, immutable/create-only and never executable.

## Usage from FaultWeave root

```powershell
python scripts/phase19c2i_readiness.py --audit
python scripts/phase19c2i_readiness.py --write
```

`--audit` reads local evidence and writes nothing. `--write` only creates `data/datasets/v2/plan/phase19c2i-readiness-audit.json` if absent. An exit code `0` with `NO_GO_BULK_COLLECTION` means the safety audit completed successfully. Exit code `2` means corrupted/missing/incompatible prerequisites; stop, inspect and do not bypass.

## Subsequent work

Implement **one reusable, class-specific preflight and postflight contract** for collecting a fault run (not nine ad hoc collectors). Start with a non-disruptive mechanism on an observed research path, gated by rate, correlated impact, 60-second activation coverage, cleanup/recovery, artifact hashes and immutability. Test it offline before authorizing even one real training run. No mass 240-run command until every family has evidence and a separate approval decision. Fresh sealed unknowns are explicitly outside these 240 known/healthy runs and must never be used to calibrate the model.

Model v1, frozen thresholds, original research data, transfer financial authority, `.env`, Git and historical diagnostic evidence are untouched.
