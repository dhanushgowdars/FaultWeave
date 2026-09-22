# Phase 7 — Leakage-safe feature engineering

Phase 7 transforms verified Phase 6 request and structured-event artifacts into
deterministic numerical windows. It does not create traffic, alter raw data, or fit a
machine-learning model.

## Window contract

The builder produces separate non-overlapping 10, 30 and 60-second feature tables. A
window is contained within one ground-truth interval and one complete run; it cannot
cross a run, split, baseline/fault/recovery boundary, or evaluation-only boundary.

| Window length | Expected feature windows |
| --- | ---: |
| 10 seconds | 3,360 |
| 30 seconds | 1,120 |
| 60 seconds | 340 |

## Observable features only

Each row contains a `features` object with request rate, latency distribution,
success/non-2xx/transport-error rates, normal user-input mix, event volume, event
failure and latency rates, downstream-call rate, and six service activity counts.

The feature object excludes identifiers, timestamps, split metadata, interval, fault
family, target service, intensity and eligibility flags. Those remain protected metadata
for later split selection and offline evaluation only.

Sealed-unknown rows remain `evaluation_only`, with both eligibility flags false. They
cannot be used for fitting, feature selection, calibration or threshold tuning.

## Commands

```text
python scripts/build_phase7_features.py
python scripts/verify_phase7.py
```

Generated outputs live under `data/datasets/final/features/`, which is intentionally
ignored by Git and can be regenerated from immutable Phase 6 artifacts.
