# Phase 11D - Class-conditional open-set support

Phase 11D combines the 60-second healthy-only Isolation Forest gate with a
class-conditional support test. XGBoost still supplies the known-fault candidate, but the
candidate is accepted only when its observable feature vector lies within the support of
that class's eligible training and validation examples.

The support score is the largest per-feature standardized deviation from the predicted
class median. Class profiles use known-fault training rows. Class limits use correctly
classified known-fault validation rows and a fixed conservative multiplier. Unknown rows
do not participate in fitting, scaling, threshold calibration or class-profile creation.

The two original sealed families have already been inspected during Phase 11 remediation.
They are therefore reported honestly as development-unknown evaluation, not as a fresh
unbiased holdout. Phase 16 must introduce a newly frozen and untouched unknown family
before making a final generalization claim.
