# Phase 12: dependency-aware incident localization

Phase 12 ranks the probable origin of every abnormal run using only observable structured
events from its baseline and fault intervals. Candidate origins include the six application
services, PostgreSQL, and every directed dependency edge in the frozen topology.

## Evidence contract

The ranker compares fault-interval evidence with the same run's baseline. It uses changes in
service and dependency failure rates, latency, transport/database errors, graph direction and
first-abnormal timing. Each result stores an ordered candidate list and concise evidence
reasons. Ground-truth target metadata is read only after ranking and is used solely to compute
Top-1 and Top-3 accuracy.

This stage processes all 180 known-fault and 40 sealed-unknown abnormal runs. Sealed unknowns
remain evaluation-only; they do not tune localization weights or thresholds. Normal runs have
no injected origin and are therefore excluded from target-accuracy calculations.

## Commands

```text
python scripts/build_phase12_localization.py
python scripts/verify_phase12.py
```

The generated report is written to `data/datasets/final/localization/report.json`. It contains
per-run rankings, evidence, exact expected targets and aggregate Top-1/Top-3 metrics.

## Phase 12B remediation

The first full 220-run evaluation exposed a propagation-bias defect: the strongest caller or
upstream dependency symptom frequently outranked the injected origin. Phase 12B keeps the
Phase 6 dataset and Phase 11 models frozen and changes localization only. It separates timeout
from connection/unavailability evidence, gives precedence to the deepest causal dependency,
recognizes unavailable callees and service-side pool/lock failures, and detects broad
entry-point load separately from downstream faults. Ground truth remains evaluation-only and
is never used to rank candidates.
