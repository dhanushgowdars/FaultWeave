# Phase 19B.4 — Explicit model-ready blind-pool selection

## Why
Phase 19B.3 proved that the `account-response-timeout` scenario affects
`payment->account`, but the **frozen model's research workload** (`/api/v1/transactions`)
does not traverse that edge. Treating the model's NORMAL prediction as a fault
classification is invalid. Training features cannot be switched to transfer traffic
without an explicit distribution audit / separately versioned model research.

## Policy
- Preserve the complete Phase 19A 6-scenario pool, selector, and historical
  commitments by default (`phase19a-v1`).
- **Opt in** to `FAULTWEAVE_MODEL_READY_BLIND_POOL=1` for the separate
  `phase19b4-research-v1` selector with **5** statically compatible scenarios:
  normal, payment service unavailable, ledger service unavailable,
  ledger response timeout, account error response.
- `account-response-timeout` stays available as a controlled transfer-workflow
  case, but is excluded from frozen-model research-window acceptance.
- The selector *version* is included in the cryptographic commitment, so old
  and new commitments cannot be silently interchanged.
- The server uses a secret random nonce and publishes only the pool size,
  selector version, and commitment until reveal. Ground truth remains sealed.
- Static route compatibility is **not** proof of fault injection. Phase 19B.3
  still requires run-correlated real fault evidence before it can declare a model
  mismatch or an accepted detection.
- `verify_phase19b.py` aborts *before running traffic* if the freshly created
  session is not from the model-ready selector. A PREPARED session may remain.
- Verifier still exits non-zero for `INVALID` or `MODEL_MISMATCH` and retains
  the persisted report. Do **not** rerun just to get an easy random selection.

## After installing
1. `python -m pytest -q tests/test_phase19b4_pool.py`
2. `python -m pytest -q` and `git diff --check`
3. Add `FAULTWEAVE_MODEL_READY_BLIND_POOL=1` to local `.env` (do not commit).
4. Rebuild only gateway, confirm all services healthy and verify setting with
   `docker compose exec -T gateway python -c "import os; print(os.getenv('FAULTWEAVE_MODEL_READY_BLIND_POOL'))"`.
5. Run **one** blind verification only after observability diagnostics and
   fault-control safety preflight; record the returned verdict.

## Out of scope
No changes to financial recovery, model v1, 65-feature schema, training,
thresholds, frontend, fault injection, existing historical run artifacts, or
legacy demonstration behavior. This is a bounded, honest v1 research-demo scope;
it does not claim model accuracy on all transfer-workflow faults.
