# FaultWeave verified project status

## Completed

- Banking microservice workflow with Gateway, Authentication, Transaction,
  Payment, Account, Ledger and PostgreSQL.
- Structured logging and correlated request, trace, transfer and evidence IDs.
- Controlled fault mechanisms for nine known fault families.
- Verified V2 dataset with 126 admitted experiments and 65 observable features.
- Frozen Candidate 04 model and exact artifact identity checks.
- Two sealed unknown evaluations retained outside model fitting and tuning.
- Frozen-model FastAPI shadow service.
- One-shot, rolling and passive live telemetry pilots.
- Authenticated Command Center bridge and persisted case reader.
- Evidence-backed first-failure localization and saved grounded explanations.
- Explicit engineer workflow for preparation, execution, analysis, approval and
  same-transfer recovery.
- Unified frontend using real backend data without the old hard-coded sample UI.
- Nine-family live acceptance matrix that distinguishes PASS, MISMATCH and NOT RUN.

## Measured scope

The verified dataset covers all nine trained families. Isolated live acceptance
coverage is reported independently and may be smaller. Development and sealed
unknown results retain their exact sample counts and are not general accuracy
claims.

## Protected artifacts

The Command Center integration does not retrain Candidate 04, rewrite the
verified dataset, reset PostgreSQL, automatically activate faults, change
Docker resources or deploy the project.
