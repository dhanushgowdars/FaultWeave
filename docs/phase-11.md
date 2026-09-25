# Phase 11: Open-set rejection

Phase 11 combines the Phase 8 anomaly gate and Phase 9 known-fault classifier
without retraining either model. Each 30-second feature window is processed in
this order:

1. The Isolation Forest decides whether the window is anomalous using its
   Phase 8 threshold.
2. A non-anomalous window is labelled `NORMAL`.
3. An anomalous window receives the XGBoost known-class prediction only when
   its maximum class confidence and its standardized distance from the
   predicted known-class centroid both meet limits calibrated from known-fault
   training and validation windows.
4. Otherwise it is labelled `UNKNOWN ABNORMAL PATTERN`.

The confidence floor is calibrated only from known-fault validation data. The
sealed unknown families are never used to select thresholds or fit a model; they
are evaluated only after the policy is frozen. Prediction records include the
anomaly score, XGBoost confidence and centroid-distance novelty score so every
rejection is inspectable.
