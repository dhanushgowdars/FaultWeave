# Remaining backend-first roadmap

Phase 15 source is preserved on its own branch. Work proceeds on phase-16/backend-transfer.

1. **Phase 16A — durable transfer core (complete).** Integrated login, accounts, transaction coordination, payment processing, atomic debit/credit, ledger confirmation, saved workflow evidence and reviewed retry. Its normal, rejection, failure, concurrency, PostgreSQL and Docker gates passed before Phase 16B began.
2. **Phase 16B — controlled fault cases and engineer review (complete).** The existing fault catalogue is connected to the durable transfer routes. Case activation, protected ground truth, cleanup, engineer review and recovery proof passed live acceptance across five controlled cases.
3. **Phase 16C — evidence and model compatibility (complete).** Transfer evidence is joined to frozen model inference only through a real overlapping telemetry window. Operational facts remain authoritative and protected fault truth is excluded from inference.
4. **Phase 16D — sustained live model acceptance (complete).** A 60-second baseline, payment outage and recovery workload passed with real customer-impact telemetry, durable review cases, engineer recovery, an evaluated frozen-model window and an `ABNORMAL` decision.
5. **Phase 16E — API contract acceptance (complete).** Versioned transfer history, exact search, engineer review queue, normalized evidence, deterministic sanitized export and frozen model-contract metadata passed authorization, restart-persistence and live Docker acceptance.
6. **Phase 16F — grounded incident assistant (complete).** Real Groq GPT-OSS 120B generation, strict output, case-only citations, redaction, authorization, caching, immutability and read-only behavior passed live Windows Docker acceptance. Unsafe output still falls back to the labelled deterministic explanation.
7. **Phase 16G — AI explanation quality acceptance (complete).** Seven saved real-Groq explanations passed the resumable quality gate at 100%, including normal, five awaiting-review cases and one recovered case. Protected fault labels remained outside provider input.
8. **Phase 17 — demo orchestration backend (complete).** Engineer-only APIs expose separate prepare, fault-arm, transfer, analysis, approval and recovery actions. Live acceptance verified persisted sessions, explicit approval, same-transfer recovery, one funds movement and a balanced journal.
9. **Phase 18 — judge command center (implemented; live Windows gate pending).** The React frontend connects the verified APIs to engineer login, a configurable transfer form, six real scenarios, timestamp-ordered workflow replay, per-case investigation, persisted case history, evidence export, grounded assistant output and separate approval/recovery controls. It does not invent service events while the backend is running.
10. **Phase 19 — model compatibility and fresh evaluation.** Recheck the frozen 65-feature contract on the completed transfer workflow and evaluate a genuinely fresh unknown family. Retrain only if the observable feature contract or behavior materially changed.
11. **Phase 20 — final evaluation and presentation.** Freeze implementation, rehearse full successful/failed/reviewed cases, document limitations and back up source, datasets, model files and database separately.

A passing unit test suite establishes code checks, not completion of every roadmap stage. Each stage must meet its stated live/evidence gate before moving on.
# Backend-first continuation status

- Phase 16A: durable PostgreSQL transfer workflow — complete.
- Phase 16B: controlled fault, review and recovery evidence — complete.
- Phase 16C: evidence-first transfer intelligence compatibility — complete.
- Phase 16D: sustained live transfer/model acceptance — complete; Windows
  Docker execution verified customer impact, durable review/recovery and an
  evaluated abnormal model window.
- Next: run the Phase 18 live Windows/Docker acceptance and then freeze the user interface before Phase 19 fresh-unknown evaluation.
