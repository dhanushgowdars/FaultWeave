# Phase 18 — judge command center

Phase 18 converts the approved FaultWeave visual design into a real React client for the
Phase 17 orchestration contract. It does not use the earlier static frontend simulation.

## Truth boundaries

- The backend exposes **six runnable scenarios**: one normal transfer and five controlled
  faults.
- Phase 16G verified **seven assistant states** because Ledger unavailable was analyzed
  both before and after recovery. Recovery is a lifecycle state, not a seventh fault.
- The UI waits for the execute API to return before replaying its persisted timeline.
  Intermediate service success is never predicted by a browser timer.
- Payment channel, names and purpose are explicitly stored as non-financial presentation
  metadata. They do not claim real UPI, card or bank-network integration.
- PostgreSQL-backed workflow, money status and paired financial records remain
  authoritative. The model adds an independent telemetry signal. The assistant is
  read-only and must cite case evidence.

## Implemented experience

1. Engineer authentication through the real Authentication service.
2. Configurable sender, recipient, amount, purpose and simulated channel.
3. Scenario catalog loaded from `GET /api/v1/demo/scenarios`.
4. Prepare, optional fault arm, real execute and grounded analyze calls.
5. Two-second readable replay of returned persisted events across the architecture view.
6. Scenario-specific Investigation content from the stored assistant response.
7. Engineer approval saved separately from same-transfer recovery.
8. Re-analysis after recovery, producing the seventh validated explanation state.
9. Numbered Case 1, Case 2, and later history for workflows completed in the current
   browser demonstration only. Older backend sessions remain persisted for audit but are
   not mixed into a fresh judge presentation.
10. Evidence tabs for ordered story, structured events, stored sources and money records.
11. Backend evidence export download including the integrity contract.
12. Honest `WINDOW_NOT_READY`, provider fallback and unavailable states.

## Local verification

```text
python -m ruff check .
python -m pytest
cd web
npm install
npm run lint
npm run test
npm run build
```

The final Windows live gate must additionally run all six scenarios against Docker,
confirm that each has its own investigation and evidence, and complete the Ledger
approval/recovery flow without a second funds movement.
