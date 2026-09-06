# FaultWeave

FaultWeave is a controlled digital-transaction microservice environment for studying log anomaly detection and probable incident-origin localization. It simulates transactions only; it does not move real money or use real customer data.

## Phase 1 status

- API Gateway, Authentication, Transaction, and Payment services
- PostgreSQL with service-owned `auth`, `transactions`, and `payments` schemas
- Shared Pydantic API contracts and signed demo access tokens
- Correlation IDs propagated through the normal request path
- Docker Compose health checks and deterministic demo seed user
- Pytest unit tests and an end-to-end smoke script

## Normal request path

`Client -> Gateway -> Authentication -> Transaction -> Payment -> Transaction completion -> PostgreSQL`

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

Container ports do not collide with equal ports in other Compose projects because each container has its own network namespace. Only the host ports on the left must be unique. All host mappings are configurable in `.env`.

To also remove the development database volume:

```bash
docker compose down -v
```

## Demo request

```bash
curl -X POST http://localhost:18110/api/v1/transactions \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-request-001" \
  -d '{"username":"demo","password":"faultweave-demo","amount_minor":12500,"currency":"INR","recipient":"merchant-demo"}'
```

Expected status: `COMPLETED`. The response contains one correlation/request ID, transaction ID, and payment ID.

## Run tests locally

```bash
python -m venv .venv
pip install -r requirements-dev.txt
pytest
```

## Repository map

```text
services/        four independently deployable FastAPI services
shared/          contracts, security, middleware, and database helpers
infra/postgres/  database schema bootstrap
scripts/         reproducible smoke verification
tests/           Phase 1 unit tests
docs/            phase acceptance criteria and architecture notes
```

See `docs/phase-1.md` for verification criteria and next-phase boundaries.
The complete gated build order is recorded in `docs/roadmap.md`.
