# Phase 19C.2L — bounded authentication-rejection diagnostic

## Important distinction

`AUTHENTICATION_FAILURE_BURST` in the existing `experiments/faults/traffic_probe.py` is implemented by **sending invalid passwords**, not by an injectable fault inside the authentication service. Its original intensity rates are **mild 20 RPS, medium 40 RPS, high 60 RPS**, while the recent healthy-stack diagnostics cover only **1.5/2.0/2.5 RPS**. Running the historical probe at 20–60 RPS is **not approved** by this step.

This implementation tests only *low-rate rejected-login observability* at **1.5 RPS**, not high-rate burst/capacity behavior. A passed pilot does not yet approve `AUTHENTICATION_FAILURE_BURST` for training or any of the 240 planned rows.

## Components

- `scripts/phase19c2l_auth_pilot.py`: opt-in, bounded invalid-login research API diagnostic. Reuses `experiments.runner.execute_plan`, `FaultStateStore` exclusive lease, exact 60-second V2 observable features, 8 valid-login recovery requests, whitelist log collector, and create-only output. It does **not** call infrastructure-stop adapters or trigger database/service outages.
- `scripts/phase19c2l_auth_audit.py`: read-only independent manifest/hash/provenance/window/features/impact/recovery check. Requires correlated `authentication`-service `http_request_completed` 401 evidence, not merely gateway statuses. Recomputes the complete 65-feature vector from allowlisted observations.
- `tests/test_phase19c2l_auth_pilot.py`: 23 offline tests for rates, provenance, trace matching, impact thresholds, cleanup, CLI and immutable output.

## Installation / non-executing verification

```powershell
$installer = "$env:USERPROFILE\Downloads\faultweave-phase19c2l-guarded-installer.py"
python $installer --check
python $installer --apply
python -m pytest -q tests/test_phase19c2l_auth_pilot.py
git diff --check
python -m scripts.phase19c2l_auth_pilot --dry-run --rps 1.5
```

`--check` and `--dry-run` do not run traffic. No old source files or plans are overwritten. If a destination already exists, the installer stops; inspect hashes rather than deleting it.

## Optional live pilot (only after green tests, operator confirmation, and healthy isolated local stack)

```powershell
python -m scripts.phase19c2l_auth_pilot --execute --rps 1.5
```

The workload uses 75-second planned capture, 105-second maximum exclusive lease, invalid-login requests and eight post-clear valid requests. Aborts if the exact 60-second window lacks genuine boundary evidence, fewer than 60 in-window clients, fewer than 90% of in-window client 401 rejections, fewer than 75% corroborated with authentication-service 401 logs, failed recovery, schema issues, or stack preflight/postflight problems. A recorded failure or missing artifact should be investigated, not automatically rerun.

One successful run creates `data/datasets/v2/pilot-fault/v2auth-<hex16>/` with `requests.jsonl`, `recovery_requests.jsonl`, `events.jsonl`, `candidate_feature.json`, `protected_ground_truth.json`, `manifest.json`. These are **diagnostic_only**, `training_eligible=false`, `threshold_tuning_eligible=false`, `label=null`.

Audit using the actual generated run ID:

```powershell
python -m scripts.phase19c2l_auth_audit --project-root . --pilot-dir "data/datasets/v2/pilot-fault/v2auth-<ACTUAL_HEX16>"
```

## Gaps and explicit safety boundaries

- No 20–60 RPS burst is permitted; high-load testing needs separate calibration/early-abort controls.
- An invalid password returning HTTP 401 is **expected application behavior**, not an injected authentication-service malfunction. The exact semantic class needs evidence beyond this path diagnostic.
- This pilot is not automatically part of Phase 19C.2K's allowlisted supervisor. **No modification to 19C.2K.**
- All 240 proposed runs remain blocked, all known/unknown training splits remain untouched, and Model v1 is frozen. No model training, Git commit, Docker changes, or source overwrite.
