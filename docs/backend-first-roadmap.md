# Remaining backend-first roadmap

Phase 15 source is preserved on its own branch. Work proceeds on phase-16/backend-transfer.

1. **Phase 16A — durable transfer core (complete).** Integrated login, accounts, transaction coordination, payment processing, atomic debit/credit, ledger confirmation, saved workflow evidence and reviewed retry. Its normal, rejection, failure, concurrency, PostgreSQL and Docker gates passed before Phase 16B began.
2. **Phase 16B — controlled fault cases and engineer review (implemented; live acceptance pending).** Connect the existing fault catalogue to the revised transfer routes. Persist case activation, expected fault ground truth (kept out of inference), expiry/cleanup, engineer review and recovery proof. Validate each advertised fault against the new path. A successful cleanup alone must never mark an incident resolved.
3. **Phase 16C — evidence and model compatibility.** Build attributable feature windows for the revised flow, validate current model inputs and predictions against fresh normal/fault runs, and measure quality. Keep single-transfer evidence and model-window inference explicit. If feature distributions require retraining, create a versioned dataset/model update with run-level splits and an untouched final evaluation set.
4. **Phase 16D — API acceptance.** Freeze the case, trace, account, transfer, investigation, model-output and review contracts. Verify restart persistence, authorization, failure cases and evidence exports. Produce reproducible CLI demonstrations before UI work.
5. **Frontend implementation.** Connect verified APIs to the login/transfer form, ordered workflow, architecture view, evidence inspection and explicit review actions. Render actual stored statuses and times. No invented predictions or automatic success/recovery scenes.
6. **Final evaluation and presentation.** Freeze implementation, evaluate the untouched fault family, rehearse full successful/failed/reviewed cases, document limitations and back up source, datasets, model files and database separately.

A passing unit test suite establishes code checks, not completion of every roadmap stage. Each stage must meet its stated live/evidence gate before moving on.
