# FaultWeave

FaultWeave is a controlled digital-transaction microservice environment for studying log anomaly detection and probable incident-origin localization. It simulates transactions only; it does not move real money or use real customer data.

## Current status

- API Gateway, Authentication, Account, Transaction, Payment, and Ledger services
- PostgreSQL with a service-owned schema for every stateful service
- Shared Pydantic API contracts and signed demo access tokens
- Run, request, and trace IDs propagated through the complete request path
- Docker Compose health checks and deterministic demo seed user
- Pytest unit tests and an end-to-end smoke script
- Versioned structured JSON logs with cross-service correlation and latency
- Recursive credential redaction and a stable operational event taxonomy
- Rotating Docker logs and validated JSONL export for later dataset generation
- Seeded normal-traffic profiles with immutable manifests and checksummed run artifacts

## Normal request path

The Gateway calls Authentication, Account, and Transaction. Transaction calls Account,
Payment, and Ledger. Payment also records its completion through Ledger. Every stateful
service uses PostgreSQL.

The payment is a simulation. A successful response proves that the normal control flow and persistence foundation work before logging, fault injection, and machine learning are introduced.

## Run with Docker

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
python scripts/smoke_test.py
```

Verify the selected ports before starting Docker:

```bash
python scripts/check_ports.py
cp .env.example .env
docker compose up --build -d
docker compose ps
python scripts/smoke_test.py
```

The port checker is safe to run again after startup: it recognizes when the current FaultWeave Compose project is already running. The smoke test generates a new request ID on every execution, so it can also be repeated without colliding with an earlier database record.

Open API docs at <http://localhost:18110/docs>. Stop the stack with:

```bash
docker compose down
```

FaultWeave uses a separate host-port range to avoid the existing ReclaimRail and older project mappings:

| Component | Host port | Container port |
| --- | ---: | ---: |
| PostgreSQL | `15434` | `5432` |
| API Gateway | `18110` | `8000` |
| Authentication | `18111` | `8000` |
| Transaction | `18112` | `8000` |
| Payment | `18113` | `8000` |
| Account | `18114` | `8000` |
| Ledger | `18115` | `8000` |

Container ports do not collide with equal ports in other Compose projects because each container has its own network namespace. Only the host ports on the left must be unique. All host mappings are configurable in `.env`.

To also remove the development database volume:

```bash
docker compose down -v
```

## Demo request

```bash
curl -X POST http://localhost:18110/api/v1/transactions \
  -H "Content-Type: application/json" \
  -H "X-Run-ID: manual-demo-001" \
  -H "X-Request-ID: demo-request-001" \
  -H "X-Trace-ID: demo-trace-001" \
  -d '{"username":"demo","password":"faultweave-demo","account_number":"FW-DEMO-001","amount_minor":12500,"currency":"INR","recipient":"merchant-demo"}'
```

Expected status: `COMPLETED`. The response contains account, transaction, payment, and
ledger identifiers. This is simulated data only.

## Run tests locally

```bash
python -m venv .venv
pip install -r requirements-dev.txt
pytest
```

## Repository map

```text
services/        six independently deployable FastAPI services
shared/          contracts, security, middleware, and database helpers
config/          frozen service-dependency contract
infra/postgres/  database schema bootstrap
scripts/         reproducible smoke verification
tests/           Phase 1 unit tests
docs/            phase acceptance criteria and architecture notes
```

See `docs/phase-1.md` for verification criteria and next-phase boundaries.
The complete gated build order is recorded in `docs/roadmap.md`.

## Phase 2B architecture-freeze gate

After rebuilding on a clean development database, verify 100 complete normal flows:

```bash
python scripts/verify_phase2b.py
```

This gate verifies the six-service timeline, seven directed dependency edges, schema
version `1.1`, correlation propagation, absence of unexpected failures, and secret
redaction. Traffic experiments and dataset generation must not begin until it passes.

## Export structured logs

After running at least one smoke transaction:

```bash
python scripts/verify_phase2.py
python scripts/collect_logs.py --since 10m
```

The generated JSONL file is written under `data/raw/` and intentionally ignored by Git.
See `docs/phase-2b.md` for the frozen schema and acceptance criteria.

## Phase 3 normal experiments

Preview a deterministic workload without sending traffic:

```bash
python -m experiments.runner --profile low --seed 31001 --duration 5 --dry-run
```

Run one short integration experiment:

```bash
python -m experiments.runner --profile low --seed 31001 --duration 5
```

Phase 3 then calibrates the local high-but-healthy envelope before running the 25 official
normal experiments. Runtime artifacts are stored under `data/experiments/`, ignored by Git,
and verified using SHA-256 checksums. See `docs/phase-3.md` for the gated procedure.

## Phase 4 controlled faults

Phase 4 begins with an exclusive, bounded fault-activation lease and frozen registries
for seven core known faults, two extended known faults and sealed unknown identifiers.
See `docs/phase-4.md` for the staged injector acceptance process.
