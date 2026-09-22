# Phase 9: XGBoost known-fault classifier

Phase 9 trains a supervised XGBoost classifier using only fault-interval 30-second
windows from eligible training runs. Its nine classes are the declared known fault
families. Normal windows and sealed unknown windows are excluded from fitting.

Validation and test metrics are calculated on complete held-out runs. Sealed unknown
rows are deliberately excluded from classifier evaluation as well; Phase 11 will use
the Isolation Forest signal and XGBoost confidence to reject unfamiliar behaviour.
