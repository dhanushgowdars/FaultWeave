# Phase 15 - Operational intelligence dashboard

Phase 15 adds the judge-facing React interface for the frozen Phase 14 intelligence
backend. The dashboard does not fit models, calibrate thresholds or invent runtime evidence.
A localhost-only controller activates one catalogued fault at a time through the existing
bounded lifecycle. A case pauses with the controlled fault active until an engineer reviews
the evidence and approves remediation; a watchdog performs safety cleanup if the review
window expires.

## Phase 15C - case-centred live demonstration

The live demonstration is a transaction investigation rather than a global monitoring
screen. Starting a scenario creates one persistent case whose fault activation, transaction
traffic, request identifier, trace identifier, structured evidence and recovery result share
the same run identity.

The controller stores case documents in `data/live-demo/cases.sqlite3`. The dashboard shows
the real directed path through gateway, authentication, account, transaction, payment and
ledger. It marks the first failed dependency, distinguishes downstream stages that were not
reached, and displays only events correlated to the selected case. Aggregate model-window
decisions remain optional technical evidence; they are not transaction workflow stages.

Each saved case preserves exact identifiers, controller milestones, customer response,
first failure, propagation evidence, correlated service logs, optional model analysis, the
engineer's recorded decision and an independent post-cleanup transaction proving recovery.

## Information architecture

- **Overview** presents the current system decision, verified dataset scale and complete
  observe-to-recovery workflow.
- **Live Demo** runs one real, correlated transaction case and renders its directed service
  workflow, first break, propagation, customer response, evidence and recovery. Five
  recommended and four secondary scenarios use fixed safe technical parameters.
- **Case Evidence** reads persistent SQLite history with complete chronology, request-only
  structured logs, a directed dependency view and raw saved case JSON.
- **Evaluation** reports frozen offline metrics with their exact scope and limitations.
- **System** explains the service dependency graph, frozen-model boundary and runtime
  processes required by the demonstration.

The visual system uses a restrained blue/violet/cyan gradient only for the overview hero.
Operational surfaces are white with large readable typography, strong spacing and accessible
status colors. The interface avoids fake terminal output, fictional incident data, decorative
AI imagery and unsupported banking claims.

## Live evidence endpoint

The dashboard adds a bounded read-only route:

```text
GET /api/v1/intelligence/live/telemetry/recent
```

Optional filters are `limit`, `service`, `level`, `request_id` and `trace_id`. The route can
return at most 500 events and reads only telemetry already accepted into the in-memory live
buffer. It cannot inspect arbitrary container files, model artifacts, fault-control state,
dataset labels or protected ground truth.

## Run the dashboard

Start the Docker services and Docker-log telemetry bridge. Continuous background traffic is
not required because the case runner sends its own correlated transaction traffic. Then run:

```bash
cd web
npm install
npm run dev
```

Start the host-local demo controller in another terminal:

```bash
python scripts/live_demo_control.py
```

It binds only to `127.0.0.1:18116`. Vite proxies `/demo-control` to that process. The
controller validates the catalog choice, requires explicit confirmation, enforces an
exclusive lease and fixes safe scenario parameters. It keeps the fault active at an
`AWAITING_REVIEW` gate, accepts an explicit remediation approval, then verifies recovery and
persists the complete evidence package in SQLite.

The case API reports contract version `5.0`. The dashboard refuses older controller versions
so a stale process cannot render incomplete scenario data. Version 5 exposes separate fault
injection, transaction, remediation and recovery-verification actions. It records exact
transaction start/end timestamps and separates the controlled cause, original customer
request, engineer review, remediation and new recovery transaction.

The complete judge-facing contract is frozen in `docs/phase-15f.md`.

Open <http://localhost:5173>. Vite proxies `/api` and `/health` to the gateway on port 18110.

## Verification

```bash
python -m pytest
python -m ruff check services/gateway/app/intelligence_live.py \
  services/gateway/app/main.py tests/test_live_telemetry_api.py
cd web
npm run build
npm run lint
npm run test
```

The browser never receives Docker access. Infrastructure mutation remains inside the local
host controller, while the gateway continues to accept observable telemetry only.
