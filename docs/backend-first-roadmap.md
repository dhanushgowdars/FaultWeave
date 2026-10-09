# Remaining backend-first roadmap

Phase 15 source is preserved on its own branch. Work proceeds on phase-16/backend-transfer.

1. **Phase 16A — durable transfer core (complete).** Integrated login, accounts, transaction coordination, payment processing, atomic debit/credit, ledger confirmation, saved workflow evidence and reviewed retry. Its normal, rejection, failure, concurrency, PostgreSQL and Docker gates passed before Phase 16B began.
2. **Phase 16B — controlled fault cases and engineer review (complete).** The existing fault catalogue is connected to the durable transfer routes. Case activation, protected ground truth, cleanup, engineer review and recovery proof passed live acceptance across five controlled cases.
3. **Phase 16C — evidence and model compatibility (complete).** Transfer evidence is joined to frozen model inference only through a real overlapping telemetry window. Operational facts remain authoritative and protected fault truth is excluded from inference.
4. **Phase 16D — sustained live model acceptance (complete).** A 60-second baseline, payment outage and recovery workload passed with real customer-impact telemetry, durable review cases, engineer recovery, an evaluated frozen-model window and an `ABNORMAL` decision.
5. **Phase 16E — API contract acceptance (implemented; live gate pending).** Versioned transfer history, exact search, engineer review queue, normalized evidence, deterministic sanitized export and frozen model-contract metadata are implemented. The live gate must still prove restart persistence on Windows Docker before this phase is committed.
6. **Phase 16F — grounded incident assistant.** Assemble only case-scoped sanitized evidence, integrate a provider-neutral structured assistant, validate every evidence citation and retain deterministic fallback behavior. The assistant is read-only and cannot move funds, control faults, approve recovery or override model/database state.
7. **Phase 17 — demo orchestration backend.** Expose safe, separate actions for controlled fault activation, customer transfer, engineer acknowledgement, remediation and independent recovery verification. No automatic transaction or automatic recovery scene.
8. **Phase 18 — frontend implementation.** Connect verified APIs to the login/transfer form, ordered workflow, architecture view, evidence inspection, assistant explanation and explicit review actions. Render actual stored statuses and times. No invented predictions.
9. **Phase 19 — model compatibility and fresh evaluation.** Recheck the frozen 65-feature contract on the completed transfer workflow and evaluate a genuinely fresh unknown family. Retrain only if the observable feature contract or behavior materially changed.
10. **Phase 20 — final evaluation and presentation.** Freeze implementation, rehearse full successful/failed/reviewed cases, document limitations and back up source, datasets, model files and database separately.

A passing unit test suite establishes code checks, not completion of every roadmap stage. Each stage must meet its stated live/evidence gate before moving on.
# Backend-first continuation status

- Phase 16A: durable PostgreSQL transfer workflow — complete.
- Phase 16B: controlled fault, review and recovery evidence — complete.
- Phase 16C: evidence-first transfer intelligence compatibility — complete.
- Phase 16D: sustained live transfer/model acceptance — complete; Windows
  Docker execution verified customer impact, durable review/recovery and an
  evaluated abnormal model window.
- Next: Phase 16E API contract and evidence-export acceptance.
