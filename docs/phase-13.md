# Phase 13: incident severity and detection delay

Phase 13 adds two incident-level outputs without changing the frozen Phase 11D decision
pipeline or Phase 12 localization logic.

## Severity

Severity is deterministic and explainable. It is calculated only from observable 10-second
request/event features during the incident and the same run's baseline. The score combines:

- 40% failure/outcome impact,
- 25% p95 latency degradation,
- 20% affected-service breadth, and
- 15% anomaly persistence.

The score is mapped to `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`. Fault family, expected
origin, run identity, split metadata and other protected ground-truth fields are never used
as severity inputs.

## Detection delay

Delay evaluation uses a separate healthy-only Isolation Forest on the already generated
10-second rich feature table. Its scaler/model are fitted only on eligible healthy training
windows and its threshold is selected only from eligible non-unknown validation windows.
The original development-unknown families contribute zero fitting or threshold-tuning rows.

A detection is confirmed when a completed 10-second fault window crosses the frozen anomaly
threshold. Offline detection delay is therefore:

`first anomalous window end - injected fault start`

Using the window end avoids claiming a detection before the evidence window was available.
The injected start timestamp is used only after anomaly scoring to evaluate delay; it cannot
change the anomaly decision.

The 10-second temporal detector exists only to measure time-to-detection. It does not replace
or retune the frozen 60-second Phase 11D open-set system.

## Artifacts

Run:

```text
python scripts/build_phase13_impact.py
python scripts/verify_phase13.py
```

The report is written to `data/datasets/final/impact/report.json`. Phase 16 will perform the
final scientific evaluation, including a newly frozen untouched unknown family as required by
Phase 11D.
