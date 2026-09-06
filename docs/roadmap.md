# FaultWeave implementation roadmap

Each phase has an acceptance gate. We do not continue until its tests and evidence pass.

| Phase | Deliverable | Verification gate |
| --- | --- | --- |
| 1 | Repository, Compose, PostgreSQL, shared contracts, normal transaction flow | Healthy containers, completed smoke transaction, tests pass |
| 2 | Structured JSON logging | Every service emits the versioned event schema with request correlation and no secrets |
| 3 | Traffic generator | Reproducible normal workloads at low, medium, and high-but-healthy rates |
| 4 | Controlled fault injection | Each injector is time-bounded, reversible, labelled, and produces ground truth |
| 5 | Dataset generation | Normal and known-fault runs exported with run manifests and leakage-safe splits |
| 6 | Feature engineering | Fixed time-window features are deterministic and traceable to source events |
| 7 | Isolation Forest | Trained only on normal training windows; reports anomaly score and threshold metrics |
| 8 | XGBoost | Trained only on declared known faults; evaluated on held-out runs, not random rows |
| 9 | Unknown rejection | Held-out fault excluded from classifier fitting and reported as `UNKNOWN ABNORMAL PATTERN` when rejection rules fire |
| 10 | Incident correlation and localization | NetworkX correlates windows, affected services, propagation timing, and probable origin |
| 11 | Detection API | Versioned inference endpoints return evidence, uncertainty, severity, and delay |
| 12 | React dashboard | Live incident timeline, scores, affected services, and probable-origin explanation |
| 13 | Final evaluation | Repeated seeded experiments, baselines, confusion matrices, unknown-detection results, and limitations |

## Current fault taxonomy

Known classifier families are planned as:

- `DATABASE_HIGH_LATENCY`
- `DATABASE_UNAVAILABLE`
- `SERVICE_UNAVAILABLE`
- `DOWNSTREAM_TIMEOUT`
- `AUTHENTICATION_FAILURE_BURST`
- `HIGH_LOAD`
- `CONNECTION_POOL_EXHAUSTION`

The currently held-out evaluation family is `INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE`. It must not appear in XGBoost fitting, threshold tuning, feature selection, or calibration data. Its label is used only after inference to measure whether unknown rejection worked.

Database lock contention and downstream error burst remain candidates. Before dataset generation, each must be assigned exactly once as either a known class, an unknown evaluation fault, or out of scope. This prevents accidental training leakage.

## Scientific wording

- Output `probable_originating_service`, never a guaranteed root cause.
- Output `UNKNOWN ABNORMAL PATTERN`, not an invented fault name, when confidence or novelty checks reject all known classes.
- Separate anomaly detection, known-fault similarity, unknown rejection, and origin localization metrics.
- Split train/validation/test data by complete experiment run to prevent neighbouring windows from leaking across splits.
