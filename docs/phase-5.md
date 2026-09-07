# Phase 5 — Smoke Dataset

Phase 5 validates the scientific dataset contract at small scale before expensive final
generation. It is deliberately split into two gates.

## Gate 5A: deterministic run matrix

The plan contains exactly 55 complete experiment runs:

- 10 normal runs: two seeds for each frozen normal traffic profile.
- 45 known-fault runs: five replicates for each of the nine classifier classes.
- 0 sealed unknown runs.

Normal smoke runs last 12 seconds. Known-fault smoke runs use contiguous 5-second
baseline, 8-second fault and 5-second recovery intervals. These shortened durations are
for instrumentation and data-quality validation only; Phase 6 uses the final research
durations.

The plan is deterministic except for its creation timestamp. Targets and intensities are
cycled reproducibly across each fault definition. Runtime plans and generated datasets
are excluded from Git.

Writing an unchanged plan is idempotent and preserves its checksum so valid completed
runs remain resumable. Connection-pool exhaustion always uses high intensity because the
smoke acceptance gate requires actual full-pool exhaustion, not merely reduced spare
capacity.

```text
python -m datasets.smoke_plan
python scripts/verify_phase5_plan.py
```

## Gate 5B: execution and artifact integrity

Gate 5B executes the frozen plan with resume support. It will keep raw service events,
request observations, protected ground truth and manifests in separate paths. Acceptance
requires checksums, run correlation, interval coverage, cleanup/recovery, class counts,
and absence of secrets or labels from observable event records.

The two sealed unknown families are not part of Phase 5. They remain evaluation-only and
cannot be used to select features or tune any model or rejection threshold.

The executor accepts `--dry-run`, `--limit` and `--run-id`. A run is resumed only when
its manifest, plan hash and all three artifact hashes are valid. Request observations do
not contain interval or fault labels; actual interval timestamps and class metadata exist
only in the protected ground-truth artifact.

```text
python -m datasets.smoke_executor --dry-run
python -m datasets.smoke_executor --limit 2
python scripts/verify_phase5.py --minimum-runs 2
python -m datasets.smoke_executor
python scripts/verify_phase5.py --require-complete --minimum-runs 55
```

## Gate 5C: dataset-quality profile

Gate 5C derives a reproducible quality report from the accepted artifacts without
changing raw observations or protected ground truth. It requires the exact 10/45 class
balance, five runs for each known-fault class, complete request/event correlation for
all 55 runs, required observable fields, and valid interval timings. Sealed unknowns
are explicitly absent from this training-eligible smoke dataset.

```text
python scripts/verify_phase5c.py
```
