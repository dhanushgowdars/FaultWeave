# FaultWeave gated implementation roadmap

This file mirrors the finalized research plan. A phase cannot begin until the previous
phase's verification gate passes. Frontend presentation may adapt; research scope,
dataset integrity, model separation, unknown-fault isolation, and evaluation cannot.

| Phase | Deliverable | Non-negotiable gate |
| --- | --- | --- |
| 0 | Research contract and scope freeze | Controlled simulation; no fraud claim, real bank, guaranteed root cause, or automatic repair |
| 1 | Core backend | Gateway, Authentication, Transaction, Payment, PostgreSQL; normal flow and tests pass |
| 2 | Structured observability | Versioned JSON logs, correlation, latency/status, redaction, export |
| 2B | Architecture and observability freeze | Add Account and Ledger; schema `1.1`; 100 clean correlated flows; restart clean |
| 3 | Experiment and normal-traffic harness | Seeded run manifests; calibrated low, medium, and high-but-healthy traffic |
| 4 | Controlled known-fault injection | Every injector time-bounded, reversible, labelled, and smoke-verified |
| 4C | Sealed unknown registry | Unknown families isolated before feature or threshold work |
| 5 | Smoke dataset | Small runs reveal instrumentation, separability, recovery, and leakage problems |
| 6 | Final dataset | Raw events, manifests, ground truth, and run-level splits are separate and immutable |
| 7 | Feature engineering | Deterministic window features; 10/30/60-second comparison; no label leakage |
| 8 | Rule-based baseline | Non-ML baseline evaluated before ML |
| 9 | Isolation Forest | Fit on healthy training windows only |
| 10 | XGBoost | Fit only on known abnormal classes; calibrated on allowed validation runs |
| 11 | Open-set rejection | IF strength, XGBoost confidence, and novelty produce `UNKNOWN ABNORMAL PATTERN` |
| 11B | Leave-one-known-fault-out validation | Rejection tuned without using sealed unknown families |
| 12 | Incident correlation and localization | NetworkX incident graph and `probable_originating_service`, never guaranteed root cause |
| 13 | Severity and detection delay | Deterministic severity; delay measured from injection start to first confirmed anomaly |
| 14 | Live inference API | Offline-trained artifacts loaded by API; SSE updates; no Kafka |
| 15 | React dashboard | Command Center, Incident Explorer, Incident Detail, Experiment Lab, Evaluation |
| 16 | Scientific evaluation | Normal FP, known/unknown metrics, origin top-1, delay, baseline, ablation |
| 17 | Hardening and final demo | Reproducible known DB-latency and unknown intermittent-connection demos |

## Frozen service topology

- Gateway calls Authentication, Account, and Transaction.
- Transaction calls Account, Payment, and Ledger.
- Payment calls Ledger.
- Authentication, Account, Transaction, Payment, and Ledger use PostgreSQL.

The machine-readable directed edges are in `config/service_dependencies.json`.

## Fault registry

Known classifier classes:

1. `DATABASE_HIGH_LATENCY`
2. `DATABASE_UNAVAILABLE`
3. `SERVICE_UNAVAILABLE`
4. `DOWNSTREAM_TIMEOUT`
5. `AUTHENTICATION_FAILURE_BURST`
6. `HIGH_LOAD`
7. `CONNECTION_POOL_EXHAUSTION`
8. `DATABASE_LOCK_CONTENTION`
9. `DOWNSTREAM_ERROR_BURST`

Sealed unseen evaluation families:

- `INTERMITTENT_DOWNSTREAM_CONNECTION_FAILURE`
- `LATENCY_JITTER_PARTIAL_DEGRADATION`

The unseen runs are forbidden from XGBoost fitting, feature selection, Isolation Forest
threshold tuning, classifier calibration, and novelty-threshold selection. Ground-truth
labels are used only after inference for evaluation.

## Dataset contract

- First generate a smoke dataset: 10 normal runs and 5 runs per known class.
- Target final scale: about 60 normal, 180–220 known-fault, and 30–40 unseen runs.
- Each run contains baseline, fault, and recovery intervals (initially 0–30, 30–90,
  and 90–120 seconds).
- Store raw events, run manifests, ground truth, processed features, and split assignments
  separately.
- Never expose fault labels, origin labels, or `fault_active` to model features.
- Split complete runs into train/validation/test (target 60/20/20); never randomly split
  neighbouring rows or windows.

## Modelling contract

- Initial feature windows are 30 seconds with a 5-second step; later compare 10, 30,
  and 60 seconds.
- Train Isolation Forest only on healthy training windows.
- Train XGBoost only on declared known abnormal classes.
- Reject unfamiliar behaviour using anomaly strength, calibrated classifier confidence,
  and nearest-neighbour or centroid novelty.
- Use leave-one-known-fault-out experiments to select rejection logic without touching
  the sealed unseen registry.
- Correlate service anomalies into incidents using dependency direction, time, shared
  run/request/trace IDs, first-abnormal timing, and propagation evidence.

Do not add Kafka, Kubernetes, an LLM chatbot, real banking, or automatic remediation.
