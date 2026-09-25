# Phase 11C - Full-data feature remediation

Phase 11B exposed an information bottleneck rather than a training failure. The original
33-feature schema aggregated more than three million structured events, but discarded
within-window jitter, tail latency, error-type and dependency-specific failure evidence.
Consequently, sealed latency jitter resembled the known database-latency class and
intermittent connection failures resembled known downstream failures.

Phase 11C rebuilds the derived feature datasets from every accepted raw run. It adds
observable-only request and event latency dispersion, p99 and tail-ratio measures,
error-type rates, dependency failure rates, per-service failure and latency measures,
and per-downstream failure rates. Identifiers, split fields, fault labels and ground-truth
metadata remain outside model inputs.

The rebuild writes to `features-rich-v2` and `models/rich-v2`, preserving the earlier
artifacts for comparison. Isolation Forest, XGBoost and leave-one-known-fault-out open-set
calibration are retrained from the new schema. Sealed unknown rows remain evaluation-only;
their labels cannot influence feature selection, fitting, thresholds or model selection.
