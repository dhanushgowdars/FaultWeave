# Phase 19B.3 — Fault Observability Contract

## Purpose

Distinguish an **invalid research-workload/fault combination** from an actual frozen-model error. Phase 19B.1's feature capture and Phase 19B.2's challenge-only window remain unchanged. The v1 artifacts and calibrated thresholds are frozen.

## Verified path mismatch

The sustained observable workload sends `/api/v1/transactions`. Its research dependencies include `payment->ledger`, but **not `payment->account`**. The blind `account-response-timeout` scenario injects on `payment->account` in the separate durable transfer workflow. Accordingly, a correct blind transfer failure does not establish that the sustained model-window traffic was fault-affected. The October 11, 2026 run `demo-3b13bc931eda4c12` (210/210 successful sustained challenge requests, NORMAL model decision) is an **invalid fault-specific ML acceptance sample**, not a proven ML false negative.

## Method and safety

- During the bounded 60s challenge-only window, record only observables: timestamp, run-correlated structured event counters, services and dependency edges. Do not query the selected scenario, fault metadata, nonce, or expected label. Do not feed these counters into model v1; inference is unchanged.
- After the commitment is revealed, validate static route support and evidence that the fault mechanism affected the **same recorded model window**. Fail closed for unsupported paths, missing correlated events or missing 5xx/timeout proof.
- Separate the three outcomes: `ACCEPTED` (observable and correctly predicted), `MODEL_MISMATCH` (observable but incorrect prediction), `INVALID` (cannot evaluate prediction scientifically). A failure or mismatch must produce a nonzero verification exit code; existing acceptance files remain immutable.
- The route graph in `phase19b3_observability.py` is an explicit v1 contract. Any future workload route changes require a code review and updated tests. Static reachability is **necessary but not sufficient**; observed run-correlated errors are mandatory for faulted challenges.
- `services/gateway/app/demo_api.py` is extended by one guarded observable-evidence collection hook after frozen inference and before recovery. No recovery, fault injection, financial records, sealed scenario selection or model-training logic is changed.

## Validate

`python -m pytest -q tests/test_phase19b3_observability.py`

`python -m pytest -q`

`git diff --check`

Read-only retrospective audit (no scenario replay):

`python scripts/phase19b3_audit.py data/experiments/phase19b-acceptance/acceptance-20261011T072036Z.json`

Expected retrospective verdict: `INVALID / FAULT_NOT_IN_RESEARCH_WORKLOAD_PATH`.

Do **not** run another randomized challenge until the route and evidence contract is independently reviewed. Even a successfully applied Phase 19B.3 audit patch does not repair the known blind scenario/workload mismatch. In particular, do not relabel the fault, replace the research request route wholesale or retrain frozen Model v1.
