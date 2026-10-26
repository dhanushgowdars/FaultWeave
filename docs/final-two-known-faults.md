# FaultWeave — Last two known fault families (diagnostic pilots)

These two adapters extend the existing Phase 19C.2K **diagnostic supervisor**. This is an academic/local development tool, NOT a production outage tester, global approval gate, or training collector.

## Purpose

- **DATABASE_UNAVAILABLE**: single bounded experiment that explicitly stops only the local FaultWeave PostgreSQL container (`docker compose stop postgres`), verifies its running state is false, generates correlated synthetic transaction traffic, *always attempts* `docker compose up -d --no-deps --wait postgres` in `finally`, waits for seven healthy containers, and verifies eight completed recovery transactions. The fault cannot be considered verified without clients experiencing observable failures and trace-correlated server events. The process must not be forcibly killed. In case of a hard crash, the operator must manually restore PostgreSQL with `docker compose up -d --no-deps postgres` and confirm `docker compose ps` before trying anything else. No volumes are deleted or recreated by the script.
- **HIGH_LOAD**: run-scoped, bounded **8 RPS** traffic for 75 seconds through the existing gateway transaction research path; source is the existing `experiments.runner.execute_plan`. The 1.5 RPS verified healthy reference pilot is used for p95 comparison. High volume alone is NOT treated as proven overload: the observed 60-second window must show trace-matched gateway throughput, client failures, or a meaningful latency increase. If elevated volume does not degrade performance, the pilot is rejected as `ELEVATED_TRAFFIC_ONLY_NOT_PROVEN_OVERLOAD`, never mislabeled as a successful fault.

Both use exactly 60 seconds of strict, observable feature extraction (65 unchanged features), protected fault metadata separate from model features, immutable artifact files, independent re-computation, eight same-run recovery requests, and the existing supervisor's exclusive attempt lock and journal.

## Restricted configurations

| Fault | Target | RPS | Duration | Fault lease |
|---|---|---:|---:|---:|
| DATABASE_UNAVAILABLE | postgresql | 1.5 | 75 seconds | 145 seconds |
| HIGH_LOAD | gateway | 8.0 | 75 seconds | 145 seconds |

**Postgres outage is disruptive to all microservices.** Use it **only** on the local development Docker Compose project `faultweave`, while no other users, jobs, data collection, or important DB work are running. It requires explicit acknowledgement through the controller. Never run this against production or shared Docker. High-load also has a cap, is not a capacity benchmark, and does not claim 20–60 RPS is safe.

## Usage

After installation and offline tests:

```powershell
python -m scripts.phase19c2k_collection_controller --status
python -m scripts.phase19c2k_collection_controller --pilot --kind high-load --target gateway --rps 8 --dry-run
python -m scripts.phase19c2k_collection_controller --pilot --kind database-unavailable --target postgresql --rps 1.5 --dry-run
```

Live calls require `--execute-pilot --attempt-id <unique-name> --acknowledge-diagnostic-only`. For a **database outage**, the user must also supply `--acknowledge-local-db-outage` to the supervisor; it is forbidden for other faults. Never execute both pilots concurrently.

Do **not** run the two pilots together. After each run, inspect results and supervisor status. If stopped, preserve the incomplete journal and lock, and inspect actual Docker/postgres health. **Do not clear the lock or repeat the attempt ID.**

The 240 originally planned Model v2 collection runs remain **blocked**. These diagnostic pilot results are **not training eligible**, **not a fresh unknown holdout**, and cannot be silently added to the train/validation/test splits. Model v1, the React dashboard, existing datasets, Git history and API are not modified.
