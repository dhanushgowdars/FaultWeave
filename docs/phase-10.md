# Phase 10: Transparent rule-based baseline

Phase 10 derives explicit healthy-envelope rules from only normal training windows.
Each rule is a maximum observed healthy value for an observable latency or error-rate
feature. A window is anomalous when one or more rules trigger.

The baseline does not use labels, fault identifiers or sealed unknown data to fit its
thresholds. It is evaluated on held-out known-fault and sealed-unknown partitions for
comparison with the learned models.
