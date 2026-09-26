# Phase 14D - Live telemetry and rolling feature windows

Phase 14D bridges the frozen Phase 14C inference endpoint to real runtime observations. It
adds an in-memory, event-time rolling telemetry buffer to the gateway and a host-side bridge
that tails the existing structured Docker logs. No model is fit, recalibrated or modified.

The telemetry contract accepts only schema-valid structured log events. Health probes and
FaultWeave intelligence endpoints are excluded so the monitoring path cannot feed its own
requests back into the model. Events are deduplicated by their canonical payload before they
enter the rolling buffer.

The live feature builder intentionally reproduces the frozen Phase 11C feature mathematics.
Dedicated regression tests compare the live request/event feature calculations directly with
`datasets/final_features.py`. Request observations are derived from observable gateway
completion events: HTTP 401 maps to the existing `invalid_login` bucket, 404 to
`invalid_account`, 422 to `invalid_amount`, and other responses to `valid`. Responses 200,
401, 404 and 422 are expected application outcomes; operational 5xx responses are unexpected.
No fault family, injected interval, dataset label, split or expected origin is available to the
live builder.

Two event-time windows are exposed:

- 10 seconds for the temporal Isolation Forest;
- 60 seconds for the frozen main Isolation Forest, XGBoost and open-set policy.

A window remains HTTP 409 until the telemetry watermark has covered its full duration. This
prevents a newly started collector from treating a partially observed 60-second interval as a
complete model input. `GET /api/v1/intelligence/live/features/{window_seconds}` exposes the
current observable feature window, while `GET /api/v1/intelligence/live/infer/{window_seconds}`
passes that exact vector through the already frozen Phase 14C inference function.

For continuous local monitoring, run the Docker stack and then start:

```bash
python scripts/live_telemetry_bridge.py
```

The bridge begins with a short log lookback to warm the rolling buffer, follows Docker logs,
filters monitoring feedback, and posts bounded batches to the gateway. The Phase 14D verifier
runs 62 seconds of ordinary low traffic, replays the resulting real structured telemetry into
the gateway, and requires both the 10-second and 60-second feature contracts to execute.

Phase 14D does not yet create or persist incidents. Incident identity, baseline-relative
severity, probable-origin evidence and lifecycle state are the next gate.
