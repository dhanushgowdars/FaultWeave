# Phase 8: Isolation Forest anomaly detection

Phase 8 trains an Isolation Forest on the immutable Phase 7 30-second feature table.
The model learns healthy behaviour only: eligible `train` rows from normal runs. It does
not use labels, fault identifiers, run identifiers, targets, timestamps, split values, or
sealed-unknown rows as inputs.

The scaler is fitted exclusively on normal training windows. The anomaly threshold is
selected from eligible validation rows containing normal and declared known-fault windows.
The test split reports known-fault anomaly metrics. Sealed unknown rows are then evaluated
separately and never influence fitting or threshold selection.

Artifacts under `data/datasets/final/models/isolation-forest/` are reproducible derived
outputs: serialized model, metadata, metrics, input checksums, threshold and configuration.
They are ignored by Git and can be regenerated with `scripts/train_phase8_isolation_forest.py`.
