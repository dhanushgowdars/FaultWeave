# FaultWeave — Phase 19C.2E candidate data-collection matrix

**Status:** *DRAFT; NOT APPROVED; NOT AN EXECUTION PLAN*. Prepared after independent healthy and fault pilot audits on 11 October 2026.

## Verified prerequisites

- Healthy pilot `v2pilot-01f0974b69ce4220`: 3/3 integrity checksums; all 150 client request/trace correlations match; **120 requests in the exact 60-second feature window**, 65/65 reproduced features; event errors and failures 0.
- Known-fault pilot `v2fault-afd5cd1cfbc44c65`: 5/5 checksums; **120 in-window requests, 74 affected, 74 correlated payment HTTP 500s**, 8/8 post-clear recovery requests; 65/65 feature parity. These are **diagnostic only**.
- Frozen Model v1 and all 280 original manifest and raw traces remain unchanged.

## Proposed *candidate* v2 matrix

| Candidate class | Number of separate runs | Proposed partition | Additional permission/safety gate |
| --- | ---: | --- | --- |
| Healthy / normal (three distinct regimes) | 60 | 36 train / 12 validation / 12 test | Calibrate healthy envelopes |
| Each of the nine *known* fault classes | 20 each (180 total) | 12 train / 4 validation / 4 test **per class** | Verify targets, intensity, fault effect, cleanup and recovery |
| Entire preliminary eligible pool | **240** | **144 / 48 / 48** | **Blocked until calibration** |
| Prior sealed unknown cases | 0 | Not admitted | Historical regression only, **not fresh unknown holdout** |
| Genuinely new unknown family | Not specified | Independent evaluation only | Define and seal **after** training-design freeze, never use for tuning |

The candidate matrix rotates targets and intensities across regimes to reduce obvious confounding, but **20 observations/class cannot cover every rate × intensity × target combination**; target coverage must be audited before freezing it.

Three healthy load categories (`low`, `nominal`, `elevated`) are **names, not RPS values**. The only demonstrated rate is 2.0 RPS for one healthy pilot and one `DOWNSTREAM_ERROR_BURST` pilot. This does **not** authorize 2 RPS for other fault mechanisms and targets. Every row in the generated JSON has `target_rps: null`, `execution_allowed: false`, and both training/tuning eligibility flags set to false until separate review.

## Class-specific issues before collection

- `HIGH_LOAD`: requires separately calibrated healthy and overload envelopes, host capacity and service recovery gate. A 2-RPS run is not automatically a high-load fault.
- `AUTHENTICATION_FAILURE_BURST`: requires a validated burst schedule and differentiation from legitimate normal mistakes.
- `CONNECTION_POOL_EXHAUSTION`: requires controlled concurrency and safe connection cleanup; retain permitted intensity behavior.
- `DATABASE_UNAVAILABLE`: would affect global PostgreSQL availability; requires separate explicit approval of the infrastructure intervention and recovery procedure. Do not run by default.
- `DATABASE_LOCK_CONTENTION`: require safe lock-release and independent verification.
- Service and downstream faults: require edge coverage in the research workload, run-correlated failure evidence, and no unintended cross-run fault exposure.

## Mandatory data contract

1. Each feature window has **exactly 60.000 seconds**; if fault affected, the complete window lies within verified fault activation. Fault cleanup and post-clear recovery occur outside that window.
2. Run ID, request ID, trace ID, timestamps, service path, traffic regime, intensity, target and provenance are auditable. Protected truth remains separate and never enters the 65-feature model input.
3. Validate model-window watermark, actual event population, feature order/schema hash, and independent recomputation before accepting every run.
4. Preserve all original datasets and Model v1. Use only new `data/datasets/v2/` storage with create-only manifests, checksums, no overwrites, and resumable run IDs.
5. Physically vary **rates, fault intensities, targets, schedule shapes and run seeds**, not only IDs. Controls must cover comparable rates to avoid using traffic volume as a false label shortcut.
6. Split before collection by complete run; class and regime coverage must be adequate within train/validation/test. Do not let traces or near-duplicate windows cross partitions.
7. Treat pilot candidates as research diagnostics, **never promote them to training merely because their integrity audits passed**.
8. Unknown evaluation must use a *new, truly unseen* fault family or mechanism. Preserve prior sealed unknowns only as a development regression set.

## Immediate next engineering sequence

1. Create a **blocked, deterministic draft** and verify its 240 unique IDs, target validity, class coverage, and 144/48/48 split. Do not execute it.
2. Implement a low-risk rate-calibration protocol for **healthy traffic only**, with bounded concurrency and stop conditions; use 2 RPS as an existing reference, not a universal cap.
3. For each class, prototype only the minimal safe fault/target combination needed to validate *exposure* and recovery. Document exclusions.
4. Rework/freeze the plan after measured evidence; then implement idempotent batch collection, without silently changing original v1 data.
5. Evaluate feature transformations robust to different rates before selecting and freezing the v2 model architecture. No model tuning on known-test or unseen-unknown results.

**Success gate for Phase 19C.2E:** audited frozen plan with proven execution feasibility, not a model-accuracy claim.
