"""Real service handlers and SQL persistence; configurable PostgreSQL verification.

Default: isolated SQLite files, with a test-only mutex adapter. Set
FW_TRANSFER_TEST_DATABASE_URL to run against PostgreSQL with production locks.
Each PostgreSQL test creates and removes only its own random fwtest_* schemas.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from app.main import app as gateway_app
from fastapi import FastAPI, HTTPException
from faultweave_common import transfer_http
from faultweave_common.middleware import CorrelationMiddleware
from faultweave_common.security import create_access_token, decode_access_token, hash_password
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.account.app import main as account
from services.auth.app import main as auth
from services.ledger.app import models as ledger_models
from services.ledger.app import transfer_api as ledger_api
from services.payment.app import models as payment_models
from services.payment.app import transfer_api as payment_api
from services.transaction.app import models as transaction_models
from services.transaction.app import transfer_api as transaction_api


class ServiceNetwork(httpx.AsyncBaseTransport):
    def __init__(self, apps):
        self.apps = apps
        self.blocked = set()
        self.drop_account_reply = False
        self.visited = []

    async def handle_async_request(self, request):
        host = request.url.host
        self.visited.append((host, request.method, request.url.path))
        if host in self.blocked:
            return httpx.Response(503, json={"detail": "Test dependency unavailable"})
        transport = httpx.ASGITransport(app=self.apps[host], raise_app_exceptions=False)
        response = await transport.handle_async_request(request)
        if (
            self.drop_account_reply
            and host == "account-service"
            and request.method == "POST"
            and request.url.path.endswith("/accounts/transfers")
            and response.status_code == 200
        ):
            self.drop_account_reply = False
            await response.aread()
            raise httpx.ReadTimeout("Lost reply after commit", request=request)
        return response

    async def aclose(self):
        pass


@asynccontextmanager
async def system(tmp_path, monkeypatch):
    models = {
        "account": account,
        "auth": auth,
        "transaction": transaction_models,
        "payment": payment_models,
        "ledger": ledger_models,
    }
    schema_names = {
        "account": "accounts",
        "auth": "auth",
        "transaction": "transactions",
        "payment": "payments",
        "ledger": "ledger",
    }
    # Obtain actual auth schema from metadata rather than assume its name.
    schema_names["auth"] = auth.User.__table__.schema
    database_url = os.getenv("FW_TRANSFER_TEST_DATABASE_URL")
    engines, factories, dependencies, schemas = {}, {}, {}, []
    for name, module in models.items():
        schema = schema_names[name]
        if database_url:
            mapped = f"fwtest_{uuid4().hex[:16]}"
            engine = create_async_engine(
                database_url, execution_options={"schema_translate_map": {schema: mapped}}
            )
            async with engine.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{mapped}"'))
            schemas.append((engine, mapped))
        else:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{tmp_path / (name + '.db')}",
                execution_options={"schema_translate_map": {schema: None}},
            )
        async with engine.begin() as conn:
            await conn.run_sync(module.Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        engines[name], factories[name] = engine, factory

        def make_dependency(bound_factory):
            async def dependency():
                async with bound_factory() as session:
                    yield session

            return dependency

        dependencies[name] = make_dependency(factory)

    mutexes = {}

    @asynccontextmanager
    async def test_lock(factory, service, subject, key):
        lock = mutexes.setdefault((service, subject, key), asyncio.Lock())
        if lock.locked():
            raise HTTPException(status_code=409, detail="Operation is already running")
        async with lock:
            yield

    if not database_url:
        for module in [account, transaction_api, payment_api, ledger_api]:
            monkeypatch.setattr(module, "operation_lock", test_lock)
    monkeypatch.setattr(account, "session_factory", factories["account"])
    account.app.dependency_overrides[account.get_session] = dependencies["account"]
    auth.app.dependency_overrides[auth.get_session] = dependencies["auth"]
    apps = {"account-service": account.app, "auth-service": auth.app}
    for name, api in [
        ("transaction", transaction_api),
        ("payment", payment_api),
        ("ledger", ledger_api),
    ]:
        app = FastAPI()
        app.add_middleware(CorrelationMiddleware, service=name)
        app.include_router(api.build_router(factories[name], dependencies[name]))
        apps[f"{name}-service"] = app
    network = ServiceNetwork(apps)
    monkeypatch.setattr(transfer_http, "transport", network)
    for name in ["AUTH", "ACCOUNT", "TRANSACTION", "PAYMENT", "LEDGER"]:
        monkeypatch.delenv(f"{name}_SERVICE_URL", raising=False)
    subject = str(uuid4())
    engineer_subject = str(uuid4())
    monkeypatch.setenv("ENGINEER_USERNAME", "transfer-engineer")
    async with factories["auth"]() as session:
        session.add_all(
            [
                auth.User(
                    id=subject, username="transfer-test", password_hash=hash_password("test-pass")
                ),
                auth.User(
                    id=engineer_subject,
                    username="transfer-engineer",
                    password_hash=hash_password("engineer-pass"),
                ),
            ]
        )
        await session.commit()
    async with factories["account"]() as session:
        session.add_all(
            [
                account.Account(user_id=subject, account_number="FW-SOURCE", balance_minor=50000),
                account.Account(user_id=subject, account_number="FW-DEST", balance_minor=0),
            ]
        )
        await session.commit()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app, raise_app_exceptions=False),
            base_url="http://gateway",
        ) as client:
            login = await client.post(
                "/api/v1/auth/login", json={"username": "transfer-test", "password": "test-pass"}
            )
            assert login.status_code == 200, login.text
            client.headers.update(
                {
                    "Authorization": f"Bearer {login.json()['access_token']}",
                    "X-Run-ID": "transfer-integration",
                    "X-Request-ID": "transfer-request",
                    "X-Trace-ID": "transfer-trace",
                }
            )
            yield client, network, factories, subject
    finally:
        account.app.dependency_overrides.clear()
        auth.app.dependency_overrides.clear()
        for engine, schema in schemas:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        for engine in engines.values():
            await engine.dispose()


def instruction(**updates):
    return {
        "source_account_number": "FW-SOURCE",
        "destination_account_number": "FW-DEST",
        "amount_minor": 12500,
        "currency": "INR",
        "idempotency_key": "test-transfer-001",
        **updates,
    }


def test_authentication_separates_customer_and_engineer_roles(tmp_path, monkeypatch):
    async def scenario():
        async with system(tmp_path, monkeypatch) as (client, _, _, _):
            customer_claims = client.headers["Authorization"].removeprefix("Bearer ")
            customer_login = decode_access_token(customer_claims)
            assert customer_login["role"] == "customer"
            engineer = await client.post(
                "/api/v1/auth/login",
                json={"username": "transfer-engineer", "password": "engineer-pass"},
            )
            assert engineer.status_code == 200
            assert engineer.json()["role"] == "engineer"
            assert decode_access_token(engineer.json()["access_token"])["role"] == "engineer"

    asyncio.run(scenario())


def test_demo_account_is_never_exposed_or_recreated_for_engineer(tmp_path, monkeypatch):
    async def scenario():
        async with system(tmp_path, monkeypatch) as (client, _, factories, subject):
            async with factories["account"]() as session:
                session.add(
                    account.Account(
                        user_id=subject,
                        account_number=account.DEMO_ACCOUNT_NUMBER,
                        balance_minor=50000,
                    )
                )
                await session.commit()

            customer_view = await client.get(f"/api/v1/accounts/{account.DEMO_ACCOUNT_NUMBER}")
            assert customer_view.status_code == 200, customer_view.text

            client.headers["Authorization"] = (
                f"Bearer {create_access_token(str(uuid4()), role='engineer')}"
            )
            engineer_view = await client.get(f"/api/v1/accounts/{account.DEMO_ACCOUNT_NUMBER}")
            assert engineer_view.status_code == 404, engineer_view.text

            async with factories["account"]() as session:
                demo_accounts = await session.scalar(
                    select(func.count())
                    .select_from(account.Account)
                    .where(account.Account.account_number == account.DEMO_ACCOUNT_NUMBER)
                )
                assert demo_accounts == 1

    asyncio.run(scenario())


async def balances(factories):
    async with factories["account"]() as session:
        rows = (
            await session.scalars(select(account.Account).order_by(account.Account.account_number))
        ).all()
        count = await session.scalar(select(func.count()).select_from(account.Transfer))
        entries = await session.scalar(select(func.count()).select_from(account.TransferEntry))
        return {row.account_number: row.balance_minor for row in rows}, count, entries


@pytest.mark.parametrize("failure", [None, "ledger", "payment", "account_reply"])
def test_complete_services_review_and_exactly_once(tmp_path, monkeypatch, failure):
    async def scenario():
        async with system(tmp_path, monkeypatch) as (client, network, factories, _):
            if failure in {"ledger", "payment"}:
                network.blocked.add(f"{failure}-service")
            network.drop_account_reply = failure == "account_reply"
            response = await client.post("/api/v1/transfers", json=instruction())
            assert response.status_code == 200, response.text
            case = response.json()
            if failure:
                assert case["state"] == "REQUIRES_REVIEW"
                evidence = (await client.get(f"/api/v1/transfers/{case['id']}")).json()
                assert (
                    evidence["funds_status"]
                    == {"ledger": "COMMITTED", "account_reply": "UNKNOWN", "payment": "UNKNOWN"}[
                        failure
                    ]
                )
                assert evidence["case_history"]["failures"]
                assert evidence["case_history"]["reviews"] == []
                assert evidence["case_history"]["recoveries"] == []
                state_before = await balances(factories)
                assert state_before[1:] == ((0, 0) if failure == "payment" else (1, 2))
                calls = len(network.visited)
                replay = await client.post("/api/v1/transfers", json=instruction())
                assert replay.json()["state"] == "REQUIRES_REVIEW"
                assert len(network.visited) == calls + 1  # gateway -> transaction only
                assert await balances(factories) == state_before
                network.blocked.clear()
                customer_authorization = client.headers["Authorization"]
                engineer_id = str(uuid4())
                client.headers["Authorization"] = (
                    f"Bearer {create_access_token(engineer_id, role='engineer')}"
                )
                reviewed = await client.post(
                    f"/api/v1/transfers/{case['id']}/resume",
                    json={"note": "Reviewed stored evidence and restored the dependency"},
                )
                assert reviewed.status_code == 200, reviewed.text
                assert reviewed.json()["state"] == "COMPLETED", reviewed.text
                client.headers["Authorization"] = customer_authorization
            else:
                assert case["state"] == "COMPLETED", response.text
            replay = await client.post("/api/v1/transfers", json=instruction())
            assert replay.json()["id"] == case["id"]
            amounts, transfers, entries = await balances(factories)
            assert amounts == {"FW-SOURCE": 37500, "FW-DEST": 12500}
            assert (transfers, entries) == (1, 2)
            evidence = (await client.get(f"/api/v1/transfers/{case['id']}")).json()
            assert evidence["state"] == "COMPLETED"
            assert evidence["funds_status"] == "COMMITTED"
            assert evidence["model_prediction"]["status"] == "NOT_EVALUATED"
            if failure:
                assert evidence["case_history"]["failures"]
                assert evidence["case_history"]["reviews"]
                assert evidence["case_history"]["recoveries"]
                assert evidence["investigation"]["cause_status"] == "recovered_after_review"
            assert all(item["trace_id"] == "transfer-trace" for item in evidence["timeline"])
            assert evidence["timeline"] == sorted(
                evidence["timeline"], key=lambda x: x["timestamp"]
            )
            journal = evidence["payment"]["evidence"]["ledger_posting"]["evidence"]["entries"]
            assert {x["direction"] for x in journal} == {"DEBIT", "CREDIT"}
            assert {host for host, _, _ in network.visited} == {
                "auth-service",
                "transaction-service",
                "payment-service",
                "account-service",
                "ledger-service",
            }

    asyncio.run(scenario())


def test_rejection_and_evidence_ownership(tmp_path, monkeypatch):
    async def scenario():
        async with system(tmp_path, monkeypatch) as (client, _, factories, _):
            failed = await client.post("/api/v1/transfers", json=instruction(amount_minor=50001))
            assert failed.status_code == 200
            assert failed.json()["state"] == "REQUIRES_REVIEW"
            assert (await balances(factories))[0] == {"FW-SOURCE": 50000, "FW-DEST": 0}
            case_id = failed.json()["id"]
            conflict = await client.post("/api/v1/transfers", json=instruction(amount_minor=100))
            assert conflict.status_code == 409
            bad_review = await client.post(
                f"/api/v1/transfers/{case_id}/resume", json={"note": "ok"}
            )
            assert bad_review.status_code == 422
            customer_review = await client.post(
                f"/api/v1/transfers/{case_id}/resume",
                json={"note": "Customer must not approve an operational recovery"},
            )
            assert customer_review.status_code == 403
            client.headers["Authorization"] = f"Bearer {create_access_token(str(uuid4()))}"
            assert (await client.get(f"/api/v1/transfers/{case_id}")).status_code == 404
            assert (
                await client.post(
                    f"/api/v1/transfers/{case_id}/resume",
                    json={"note": "Trying to access another owner's transfer"},
                )
            ).status_code == 403
            client.headers["Authorization"] = (
                f"Bearer {create_access_token(str(uuid4()), role='engineer')}"
            )
            engineering_view = await client.get(f"/api/v1/transfers/{case_id}")
            assert engineering_view.status_code == 200
            assert engineering_view.json()["state"] == "REQUIRES_REVIEW"

    asyncio.run(scenario())


def test_concurrent_duplicate_instruction(tmp_path, monkeypatch):
    async def scenario():
        async with system(tmp_path, monkeypatch) as (client, _, factories, _):
            responses = await asyncio.gather(
                *[client.post("/api/v1/transfers", json=instruction()) for _ in range(3)]
            )
            assert all(response.status_code in {200, 409} for response in responses)
            assert any(
                response.status_code == 200 and response.json()["state"] == "COMPLETED"
                for response in responses
            )
            assert (await balances(factories))[1:] == (1, 2)

    asyncio.run(scenario())


def test_journal_write_failure_rolls_back_both_balances(tmp_path, monkeypatch):
    from sqlalchemy import event

    async def scenario():
        async with system(tmp_path, monkeypatch) as (client, _, factories, _):
            engine = factories["account"].kw["bind"].sync_engine

            def fail_journal(conn, cursor, statement, parameters, context, executemany):
                if "INSERT INTO" in statement and "transfer_entries" in statement:
                    raise RuntimeError("Test journal storage failure")

            event.listen(engine, "before_cursor_execute", fail_journal)
            try:
                response = await client.post("/api/v1/transfers", json=instruction())
                assert response.json()["state"] == "REQUIRES_REVIEW"
                assert await balances(factories) == ({"FW-SOURCE": 50000, "FW-DEST": 0}, 0, 0)
            finally:
                event.remove(engine, "before_cursor_execute", fail_journal)
            client.headers["Authorization"] = (
                f"Bearer {create_access_token(str(uuid4()), role='engineer')}"
            )
            resumed = await client.post(
                f"/api/v1/transfers/{response.json()['id']}/resume",
                json={"note": "Journal storage restored and original failure inspected"},
            )
            assert resumed.json()["state"] == "COMPLETED"
            assert await balances(factories) == ({"FW-SOURCE": 37500, "FW-DEST": 12500}, 1, 2)

    asyncio.run(scenario())


@pytest.mark.skipif(
    not os.getenv("FW_TRANSFER_TEST_DATABASE_URL"), reason="Requires PostgreSQL row locks"
)
def test_competing_transfers_cannot_overdraw_postgres(tmp_path, monkeypatch):
    async def scenario():
        async with system(tmp_path, monkeypatch) as (client, _, factories, _):
            results = await asyncio.gather(
                *[
                    client.post(
                        "/api/v1/transfers",
                        json=instruction(amount_minor=40000, idempotency_key=f"competing-{number}"),
                    )
                    for number in range(2)
                ]
            )
            states = [response.json()["state"] for response in results]
            assert sorted(states) == ["COMPLETED", "REQUIRES_REVIEW"]
            assert await balances(factories) == ({"FW-SOURCE": 10000, "FW-DEST": 40000}, 1, 2)

    asyncio.run(scenario())
