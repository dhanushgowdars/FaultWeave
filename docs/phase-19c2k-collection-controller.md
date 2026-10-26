# Phase 19C.2K — Unified *Diagnostic* Collection Supervisor

## Boundaries

This is the first conservative integration layer **over the existing local collectors**. It is **not** the final 240-run Model v2 collection executor. The immutable 19C.2G proposal is kept blocked (240/240) and the 19C.2I offline readiness audit is rederived before every command. The module does not edit the proposal or readiness report, approve class/target combinations, create train/validation/test rows, compute labels for training, change Model v1, or train Model v2.

It recognizes **only** previously exercised diagnostic configurations: healthy transaction traffic, `DOWNSTREAM_ERROR_BURST` on `payment` at medium intensity, and `DOWNSTREAM_TIMEOUT` at medium intensity on `payment->ledger` and `transaction->payment`. Allowed diagnostic rates are 1.5 and 2.0 RPS. These are experimental limits, **not class-wide collection approval**. The original collectors preserve their own additional bounds and requirements; the supervisor does not bypass them. Seven other known fault families have no adapter and are explicitly denied. Each pilot gets a new UUID and `diagnostic_only` outputs; none adopts a proposed run ID/split/seed.

## Offline commands (run from project root)

```powershell
python -m scripts.phase19c2k_collection_controller --status
python -m scripts.phase19c2k_collection_controller --inspect-run v2-normal-low-01
python -m scripts.phase19c2k_collection_controller --pilot --kind timeout --target "payment->ledger" --rps 1.5 --dry-run
python -m pytest -q tests/test_phase19c2k_collection_controller.py
```

`--status`, `--inspect-run`, and `--dry-run` use no Docker or HTTP traffic and write no files. They verify the offline readiness evidence; the project's saved parent/pilot files must be present. Planned-row preview **never launches a pilot**.

## Opt-in single pilot (do not use merely to verify installation)

Only after explicit human review, with all seven local Docker Compose services healthy, no active fault and no other experiment running, a diagnostic pilot can be invoked:

```powershell
python -m scripts.phase19c2k_collection_controller --pilot --kind timeout --target "payment->ledger" --rps 1.5 --execute-pilot --attempt-id timeout-lab-001 --acknowledge-diagnostic-only
```

**This runs synthetic transactions and a bounded run-scoped fault.** The unique `--attempt-id` is an attempt journal ID, not the generated pilot ID or any of the 240 proposed run IDs. Never reuse it. For healthy pilots, omit `--target` and use `--kind healthy`; for payment errors use `--kind payment-error --target payment`. Do not run previously verified pilots again without a new reason.

The supervisor:

1. Verifies the original blocked plan plus the full 19C.2I readiness evidence.
2. Verifies the existing stack/no-active-fault gate.
3. Creates an exclusive `phase19c2k-active` lock and **create-only** `intent.json` under `data/datasets/v2/control/phase19c2k/<attempt-id>/`.
4. Launches the existing `--execute` collector as a Python module (no shell command interpolation); never copies historical data or injects new fault modes.
5. Rechecks post-run stack health, finds exactly one newly created immutable diagnostic run, independently audits it with the original fault/timeout auditor (or recomputes the healthy exact window and all 65 features), and writes **create-only** `result.json` with its manifest SHA256 and **training_eligible=false**.
6. Releases the controller lock **only on full success**.

**If the child, health gate, artifact check, or independent audit fails, the lock and any intent/evidence remain in place.** This prevents silent retry or overwrite. Inspect saved fault lease, seven service health states, output folders, and partial attempt evidence before any human-controlled recovery. Never automatically delete the lock or rerun with the same attempt ID. The supervisor does not recover an interrupted attempt on its own; `--status` shows incomplete attempt journals. Concurrent external direct collector invocations are not covered by this advisory controller lock, so ensure no other experiment is active.

## Remaining work (not claimed complete)

- Fault-specific experimental proof for 7 unvalidated families, including resource monitoring and explicit isolated infrastructure outage approval.
- Per-target, per-intensity and per-rate fault-effect qualification; partial/mild effects must be evaluated separately from the severe timeout diagnostics.
- A **separately reviewed authorization specification** for planned run execution, unique model v2 data admission and deterministic split/seed use. A verified diagnostic pilot does not authorize a planned row.
- Fresh sealed unknown holdout, class balance, run/split isolation, full dataset integrity, then Model v2 training and independent evaluation against frozen v1.
