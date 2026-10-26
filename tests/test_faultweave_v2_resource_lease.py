"""Offline tests for early release and cleanup of database fault resources.

These tests NEVER contact a database, change Docker, or activate a fault lease.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from shared.faultweave_common import fault_injection as fi
from shared.faultweave_common.logging import run_id_context


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.statements = []
    async def execute(self, statement, args):
        self.statements.append((str(statement), dict(args)))
    async def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self):
        self.connections = []
    async def connect(self):
        conn = FakeConnection()
        self.connections.append(conn)
        return conn


class FakeSession:
    def __init__(self):
        self.calls = []
    async def execute(self, statement, args):
        self.calls.append((str(statement), dict(args)))


def lease(seconds=3, run_id='v2lease-test', family='CONNECTION_POOL_EXHAUSTION', target='payment'):
    return {'activation_id': 'f'*32, 'run_id': run_id, 'fault_id':family,
            'target':target, 'intensity':'mild',
            'expires_at':(datetime.now(UTC)+timedelta(seconds=seconds)).isoformat()}


def initialize(monkeypatch, tmp_path, payload, capacity=2):
    control=tmp_path/'active_fault.json'
    control.write_text(json.dumps(payload), encoding='utf-8')
    monkeypatch.setattr(fi, 'CONTROL_PATH', control)
    monkeypatch.setattr(fi,'_LEASE_POLL_SECONDS',0.005)
    monkeypatch.setitem(fi._POOL_CAPACITY, 'mild', capacity)
    fi._HELD_CONNECTIONS.clear()
    fi._LOCK_HOLDERS.clear()
    run_id_context.set(payload['run_id'])
    return control


def test_pool_releases_when_lease_cleared(monkeypatch,tmp_path):
    async def trial():
        payload=lease()
        control=initialize(monkeypatch,tmp_path,payload)
        engine=FakeEngine()
        await fi.apply_connection_pool_exhaustion(engine,'payment')
        assert len(engine.connections)==2
        assert 'payment' in fi._HELD_CONNECTIONS
        control.unlink()
        await asyncio.sleep(0.04)
        assert all(c.closed for c in engine.connections)
        assert not fi._HELD_CONNECTIONS
    asyncio.run(trial())


def test_pool_releases_when_lease_replaced(monkeypatch,tmp_path):
    async def trial():
        payload=lease()
        control=initialize(monkeypatch,tmp_path,payload)
        engine=FakeEngine()
        await fi.apply_connection_pool_exhaustion(engine,'payment')
        control.write_text(json.dumps({**payload,'activation_id':'e'*32}))
        await asyncio.sleep(0.04)
        assert all(c.closed for c in engine.connections)
        assert not fi._HELD_CONNECTIONS
    asyncio.run(trial())


def test_pool_releases_at_expiry_without_control_clear(monkeypatch,tmp_path):
    async def trial():
        payload=lease(seconds=0.025)
        initialize(monkeypatch,tmp_path,payload)
        engine=FakeEngine()
        await fi.apply_connection_pool_exhaustion(engine,'payment')
        await asyncio.sleep(0.075)
        assert all(c.closed for c in engine.connections)
        assert not fi._HELD_CONNECTIONS
    asyncio.run(trial())


def test_lock_releases_on_clear(monkeypatch,tmp_path):
    async def trial():
        payload=lease(family='DATABASE_LOCK_CONTENTION')
        control=initialize(monkeypatch,tmp_path,payload)
        engine=FakeEngine();session=FakeSession()
        await fi.apply_database_lock_contention(engine,session,'payment')
        assert len(engine.connections)==1
        assert 'payment' in fi._LOCK_HOLDERS
        control.unlink()
        await asyncio.sleep(0.04)
        assert engine.connections[0].closed
        assert len(engine.connections[0].statements)==2
        assert 'pg_advisory_unlock' in engine.connections[0].statements[-1][0]
        assert not fi._LOCK_HOLDERS
    asyncio.run(trial())


def test_lock_no_duplicate_holder_under_concurrent_requests(monkeypatch,tmp_path):
    async def trial():
        payload=lease(family='DATABASE_LOCK_CONTENTION')
        control=initialize(monkeypatch,tmp_path,payload)
        engine=FakeEngine()
        await asyncio.gather(*(
            fi.apply_database_lock_contention(engine,FakeSession(),'payment')
            for _ in range(8)))
        assert len(engine.connections)==1
        control.unlink()
        await asyncio.sleep(.05)
        assert not fi._LOCK_HOLDERS
    asyncio.run(trial())


def test_lock_releases_on_replaced_control(monkeypatch,tmp_path):
    async def trial():
        payload=lease(family='DATABASE_LOCK_CONTENTION')
        control=initialize(monkeypatch,tmp_path,payload)
        engine=FakeEngine()
        await fi.apply_database_lock_contention(engine,FakeSession(),'payment')
        control.write_text(json.dumps({**payload,'run_id':'another-run'}))
        await asyncio.sleep(.05)
        assert engine.connections[0].closed
        assert not fi._LOCK_HOLDERS
    asyncio.run(trial())


def test_incorrect_run_does_not_hold_resources(monkeypatch,tmp_path):
    async def trial():
        payload=lease()
        initialize(monkeypatch,tmp_path,payload)
        run_id_context.set('other-run')
        engine=FakeEngine()
        await fi.apply_connection_pool_exhaustion(engine,'payment')
        assert engine.connections==[]
    asyncio.run(trial())


def test_pool_release_task_cancellation_closes_connections(monkeypatch,tmp_path):
    async def trial():
        payload=lease()
        initialize(monkeypatch,tmp_path,payload)
        conn=FakeConnection();conns=[conn]
        fi._HELD_CONNECTIONS['payment']=conns
        task=asyncio.create_task(fi._release_pool_after('payment',conns,payload))
        await asyncio.sleep(.002)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert conn.closed and not fi._HELD_CONNECTIONS
    asyncio.run(trial())


def test_lock_release_task_cancellation_closes_holder(monkeypatch,tmp_path):
    async def trial():
        payload=lease(family='DATABASE_LOCK_CONTENTION')
        initialize(monkeypatch,tmp_path,payload)
        conn=FakeConnection()
        fi._LOCK_HOLDERS['payment']=conn
        task=asyncio.create_task(fi._release_database_lock('payment',conn,41002,payload))
        await asyncio.sleep(.002)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert conn.closed and not fi._LOCK_HOLDERS
        assert any('pg_advisory_unlock' in stmt for stmt, _ in conn.statements)
    asyncio.run(trial())
