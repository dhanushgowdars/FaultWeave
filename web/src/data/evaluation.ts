export const evaluation = {
  dataset: {
    runs: 280,
    normal: 60,
    known: 180,
    unknown: 40,
    events: 3_097_678,
    requests: 155_424,
  },
  models: [
    { name: "60s anomaly detection", metric: "Known-test F1", value: 1.0 },
    { name: "Known-fault classification", metric: "Macro F1", value: 0.971781 },
    { name: "Final known-fault pipeline", metric: "Accuracy", value: 0.972222 },
    { name: "Normal recognition", metric: "Accuracy", value: 1.0 },
    { name: "Development unknowns", metric: "Rejection", value: 0.75 },
    { name: "10s temporal detection", metric: "F1", value: 0.981818 },
  ],
  unknownFamilies: [
    { name: "Latency jitter / partial degradation", value: 0.95 },
    { name: "Intermittent downstream connection failure", value: 0.55 },
  ],
  integrity: [
    "Whole experiment runs were assigned to one split only.",
    "Sealed unknown rows were excluded from model fitting and threshold calibration.",
    "Labels, run identifiers and fault metadata were excluded from model inputs.",
    "All 280 accepted runs passed checksum, correlation and recovery verification.",
  ],
} as const;
