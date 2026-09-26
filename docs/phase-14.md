# Phase 14 - Live inference API

Phase 14 turns the frozen offline intelligence pipeline into a live FastAPI service without
retraining or recalibrating any model. The phase is intentionally gated so integration work
cannot silently change research artifacts.

## Phase 14A - Frozen artifact loader

The gateway first gains a fail-closed loader for the frozen Phase 11D and Phase 13 model
artifacts. It loads the 60-second Isolation Forest, nine-class XGBoost classifier,
class-conditional open-set support metadata, and the separate 10-second temporal Isolation
Forest used for detection timing.

The loader verifies before inference is allowed:

- the main detector, classifier and open-set policy all declare 60-second windows;
- the temporal detector declares a 10-second window;
- every artifact uses the same frozen observable feature schema;
- the classifier declares exactly nine unique known fault classes;
- open-set class support limits and profiles cover exactly those nine classes;
- the open-set anomaly threshold matches the frozen main Isolation Forest threshold;
- serialized model bundles contain the expected model/scaler/class objects.

No training, threshold calibration, feature selection or unknown-fault tuning occurs in
Phase 14. Runtime code may load frozen artifacts only. Model paths are supplied through
`FAULTWEAVE_MODEL_ROOT`; the default supports local verification from the repository root.

Later Phase 14 gates will add the live feature buffer, inference orchestration, incident API
contracts and server-sent event streaming. Those gates must consume this loader rather than
loading or training models independently.


## Phase 14B - Docker artifact mount and intelligence readiness

The gateway container receives the frozen model directory through a read-only bind mount at
`/faultweave-models`. `FAULTWEAVE_MODEL_ROOT` points the Phase 14A loader at that mount. The
existing fault-control mount is retained explicitly because a service-level Compose volume
list replaces the shared volume list. No model artifact is copied into the image or made
writable by the gateway.

`GET /api/v1/intelligence/ready` performs a real lazy load of the frozen artifacts and
returns their window sizes, feature count, known-class count and open-set label only after
all Phase 14A consistency checks pass. Missing or incompatible artifacts produce HTTP 503.
The public response intentionally does not expose host/container paths or loader internals;
detailed failures remain in structured gateway logs.

The ordinary `/health/ready` endpoint remains a process/service readiness probe. Intelligence
endpoints are separately fail-closed so a clean repository can still run non-ML unit tests
without generated model files, while live inference cannot proceed unless the frozen models
are present.

Container verification after applying this gate:

```bash
docker compose config --quiet
docker compose up -d --build gateway
curl http://localhost:18110/api/v1/intelligence/ready
```

A successful response reports `status=ready`, a 60-second main window, a 10-second temporal
window and nine known classes.

## Phase 14C - Frozen inference execution

The gateway now exposes `POST /api/v1/intelligence/infer` as the execution boundary for the
frozen models. The request accepts only a window size and the exact frozen feature vector;
fault IDs, scenario labels, expected origins, dataset splits and ground-truth interval fields
are not part of the API contract.

A 10-second window runs only the temporal Isolation Forest and returns `NORMAL` or
`ABNORMAL_SIGNAL`. A 60-second window runs the frozen main Isolation Forest first. Normal
windows stop there. Abnormal windows continue through XGBoost and then through the frozen
class-conditional support policy, producing either one of the nine known fault classes or
`UNKNOWN ABNORMAL PATTERN`.

Feature keys must match the frozen schema exactly and every value must be finite. The gateway
reuses the artifact objects loaded by Phase 14A; it does not fit, calibrate, select features or
write model files during inference. The verification script posts representative offline
feature vectors to the running Docker gateway, but protected evaluation metadata is retained
only by the verifier and is never transmitted to the inference endpoint.

Phase 14C deliberately does not claim that raw Docker logs have already become live feature
windows. That telemetry/windowing bridge is the next gate. Keeping model execution separate
from telemetry acquisition lets the frozen inference contract be tested before introducing
stateful buffers and incident lifecycle logic.
