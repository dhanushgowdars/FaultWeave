# Phase 3 - Reproducible normal-traffic experiment harness

## Purpose and boundary

Phase 3 measures healthy system behaviour across repeatable workload shapes. It does not
inject operational faults, generate final ML datasets, engineer features, or train models.
Expected user mistakes are normal client behaviour and remain distinct from service faults.

## Frozen profiles

| Profile | Default load | Purpose |
| --- | ---: | --- |
| `low` | 3 req/s | Steady low normal traffic |
| `medium` | 12 req/s | Steady medium normal traffic |
| `high_healthy` | About 40 req/s, calibrated | Upper healthy operating envelope |
| `short_burst` | 5 req/s base, 20 req/s burst | Brief healthy demand variation |
| `normal_errors` | 5 req/s | More wrong-password, missing-account, and invalid-amount requests |

All profiles are deterministic for a given profile, seed, duration, and rate. Each request
gets a unique request ID and trace ID, while every request in a run shares one run ID.

`high_healthy` is normal behaviour. The later Phase 4 `HIGH_LOAD` fault must be configured
clearly above the accepted healthy envelope and cannot reuse these normal settings.

## Stored artifacts

Phase 3 writes ignored runtime files:

```text
data/experiments/
  calibration/healthy_envelope.json
  manifests/<run_id>.json
  raw/<run_id>/requests.jsonl
  raw/<run_id>/events.jsonl
  suites/<suite_id>.json
```

Request records never store credentials or request bodies. Event files contain only schema
`1.1` structured events for their run. Every manifest contains configuration, seed,
timestamps, rates, status/scenario counts, success and expected-outcome rates, latency
percentiles, achieved throughput, Git commit, artifact paths, and SHA-256 checksums.

Manifest purposes prevent scientific mixing:

- `ad_hoc`: individual development run
- `calibration`: healthy-envelope selection only
- `smoke`: shortened suite test
- `official`: five seeds for every profile at full default duration

## Verification sequence

Keep the seven Phase 2B containers healthy throughout these steps.

### 1. Deterministic dry run

```bash
python -m experiments.runner --profile low --seed 31001 --duration 5 --dry-run
```

### 2. Short real runs

```bash
python -m experiments.runner --profile low --seed 31001 --duration 5
python -m experiments.runner --profile normal_errors --seed 35001 --duration 5
python scripts/verify_phase3.py --minimum-runs 2
```

### 3. Calibrate the healthy envelope

```bash
python -m experiments.calibrate --candidates 20,30,40 --duration 10
```

A candidate is accepted only when expected-outcome rate is at least 99%, there are no
transport or server errors, achieved throughput is at least 90% of target, and p95 latency
does not exceed 1000 ms. The highest accepted candidate becomes `high_healthy` locally.

Authentication runs four Uvicorn workers by default because PBKDF2 verification is
intentionally CPU-intensive. This is service concurrency, not relaxed authentication. Demo
user creation uses an idempotent PostgreSQL conflict rule so clean multi-worker startup is
safe. `AUTH_WORKERS` remains configurable in `.env` for different development machines.

### 4. Short suite smoke

```bash
python -m experiments.suite --runs-per-profile 1 --duration 5
python scripts/verify_phase3.py --minimum-runs 7
```

The minimum is seven because the two earlier ad-hoc runs plus five smoke runs are retained.

### 5. Official normal suite

```bash
python -m experiments.suite
python scripts/verify_phase3.py --minimum-runs 25 --require-official
```

This creates 25 official runs: five independent seeds for each of five normal profiles.
Default duration is 30 seconds per run. Suite execution is resumable; a verified official
profile/seed pair is not repeated after interruption.

## Acceptance gate

- Unit tests and lint pass.
- The same profile/seed/configuration produces the same request plan.
- All five profiles execute without active fault injection.
- A machine-specific high-but-healthy rate is accepted and stored.
- All 25 official profile/seed pairs exist.
- Expected-outcome rate is at least 99% for every run.
- No transport or HTTP 5xx errors occur.
- Valid requests contain the complete six-service, seven-edge correlated path.
- Manifest counts and SHA-256 checksums match stored artifacts.
- No credentials, fault labels, `fault_active`, or origin labels appear in raw artifacts.

Only after this gate passes may controlled Phase 4 fault injection begin.
