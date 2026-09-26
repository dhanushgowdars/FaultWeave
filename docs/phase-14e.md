# Phase 14E - Live incident lifecycle, severity and probable origin

Phase 14E turns independent live anomaly windows into stateful incidents. It preserves the
frozen Phase 11D models, the Phase 12C localization logic and the Phase 13 deterministic
severity formula. No model fitting, threshold tuning or fault-family lookup occurs at runtime.

## Incident lifecycle

The incident manager evaluates one event-time decision per 10-second bucket. A new incident
opens when either the 10-second temporal detector produces `ABNORMAL_SIGNAL` or the frozen
60-second pipeline produces `ABNORMAL`. The incident remains active while abnormal evidence
persists. Resolution requires three consecutive normal 10-second decisions and a normal
60-second main decision, which provides deterministic anti-flapping without changing any ML
threshold.

The manager stores at most 100 completed incidents in memory. Restarting the gateway clears
this runtime history; durable persistence is intentionally outside Phase 14.

## Baseline and impact

At first detection the manager attempts to capture the 30 seconds of observable telemetry
immediately preceding the first abnormal window. The baseline consists of three 10-second
feature windows plus their structured events. This matches the Phase 13 baseline duration and
keeps severity based on pre-detection evidence rather than fault labels or injected intervals.

Live severity reuses the Phase 13 weights and bands:

- 40% failure impact;
- 25% latency degradation;
- 20% affected-service breadth;
- 15% temporal anomaly persistence.

The gateway retains the latest six 10-second incident feature windows for persistence. If a
collector was started too late to capture a pre-detection baseline, the incident still opens
and classifies, but baseline-relative severity and localization remain unavailable instead of
inventing a healthy reference.

## Probable-origin localization

The gateway contains a runtime copy of the frozen Phase 12C deterministic causal ranking.
Regression tests compare the live ranking directly with `datasets.incident_localization` so
future live changes cannot silently drift from the frozen offline method. Inputs remain only
structured service/dependency failures, latency changes, transport/error evidence, first
failure timing and the frozen dependency graph.

The result is explicitly a **probable origin**, not a guaranteed root cause. The API exposes
the top candidate and top three evidence records with their scores and reasons.

## API

Phase 14E adds:

```text
POST /api/v1/intelligence/live/evaluate
GET  /api/v1/intelligence/incidents/current
GET  /api/v1/intelligence/incidents
GET  /api/v1/intelligence/incidents/{incident_id}
```

`live/evaluate` advances temporal persistence and recovery counters only when the telemetry
watermark enters a new 10-second bucket, so repeated polling cannot duplicate temporal
samples. If the watermark advances inside the same bucket, the manager may still refresh the
60-second classification and probable-origin evidence without appending another temporal
sample. This prevents an incident from retaining a stale open-set decision when the newest
60-second rolling window becomes more complete. Phase 14F will drive this evaluator
continuously and publish lifecycle changes through server-sent events.

Frozen inference fails closed unless the rolling buffer contains client-observed request
telemetry. This preserves measurement parity with the Phase 11C request features: the
training request latency was measured by the traffic source, whereas the gateway middleware
measures only server-side processing time. Service-event-only telemetry may still be inspected
through the live feature endpoint, but it is marked `gateway_derived` and is not used to make
model or incident decisions.

The live incident contract never accepts or stores a fault ID, injected interval, dataset
split, training label or expected origin. The Phase 14E verification script may use recorded
ground-truth boundaries only outside the API to stage a deterministic replay; only shifted
structured runtime events and shifted client-observed request records are transmitted to the
gateway.
