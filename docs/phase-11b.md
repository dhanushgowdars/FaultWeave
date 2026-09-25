# Phase 11B: Leave-one-known-fault-out validation

Phase 11B calibrates the open-set rejection policy without inspecting either
sealed unknown family. For each of the nine known fault classes, the process
trains a temporary classifier on the remaining eight classes and presents the
excluded class as a simulated unseen pattern during validation.

The selected confidence and centroid-novelty limits maximize synthetic-unknown
F1, then recall and known-class retention. The anomaly-score, confidence and
novelty limits are all calibrated from these synthetic-unseen folds, with
healthy validation windows included to control false positives. The sealed
unknown runs remain evaluation only and are used once, after policy selection,
by Phase 11 inference.
