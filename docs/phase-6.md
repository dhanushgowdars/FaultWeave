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

## Phase 6B executor

The final executor uses unique attempt namespaces and checksum-based resume. Raw request
observations, structured events, protected ground truth and manifests remain separate.
The default `eligible` partition contains only normal and known-fault runs. Sealed
unknowns require the explicit `evaluation_only` partition and remain marked ineligible
for training and threshold tuning in both ground truth and manifests.

```text
python -m datasets.final_executor --dry-run --limit 2
python -m datasets.final_executor --run-id final-normal-low-01
python scripts/verify_phase6.py --minimum-runs 1
python -m datasets.final_executor
python scripts/verify_phase6.py --require-complete --minimum-runs 240
python -m datasets.final_executor --partition evaluation_only
python scripts/verify_phase6.py --partition evaluation_only --require-complete --minimum-runs 40
```
### Normal short-burst schedule

Final-dataset `short_burst` runs preserve the Phase 3 shape: a centered six-second peak
surrounded by 5 requests/second baseline traffic. Extending a normal run to 120 seconds
lengthens only the baseline shoulders (57 seconds each); it does not stretch the burst.
The peak is capped at the host's recorded high-but-healthy calibration boundary, preventing
a normal run from crossing into overload. The resolved schedule is stored in each new run's
ground-truth artifact.
## Phase 6C: final-dataset quality profile

After all 280 runs are complete, run `python scripts/verify_phase6c.py`. The profiler first
reuses the Phase 6 checksum and isolation verifier, then independently measures request/event
correlation, ground-truth interval assignment, per-run fault signal, clean recovery, service and
event-type diversity, and optional-field null rates grouped by event type. Its report is written
to `data/datasets/final/quality/profile.json`. Global optional-field null percentages are not used
as a quality gate because many structured event fields are intentionally event-type-specific.
