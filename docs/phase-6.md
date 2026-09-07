# Phase 6 — Final Research Dataset

Phase 6 scales the validated smoke pipeline into the immutable research dataset. Phase
6A freezes the matrix before any final traffic is generated.

## Phase 6A matrix

- 60 normal runs: 12 replicates for each of five frozen traffic profiles.
- 180 known-fault runs: 20 replicates for each of nine classifier classes.
- 40 sealed-unknown runs: 20 replicates for each of two evaluation families.
- Every run lasts 120 seconds. Fault runs use 30-second baseline, 60-second fault and
  30-second recovery intervals.

The 240 eligible normal and known-fault runs are stratified by complete run into 144
training, 48 validation and 48 test runs. No event, request, or later feature window may
cross a run boundary. The 40 sealed-unknown runs use the `evaluation_only` split and set
both `training_eligible` and `threshold_tuning_eligible` to false.

```text
python -m datasets.final_plan
python scripts/verify_phase6_plan.py
```

The plan writer is idempotent: an unchanged contract preserves its existing timestamp
and checksum so resumable execution remains valid.
