"""Isolated DB-outage fixture for FaultWeave v2.

Never stops the original FaultWeave PostgreSQL container; all Docker changes
are confined to project faultweave-v2-isodb. No volume deletion commands.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'faultweave-v2-isodb'
FIXTURE = ROOT / 'data' / 'v2-isolated-db'
OVERRIDE = FIXTURE / 'compose.override.yml'
PORTS = {
    'GATEWAY_HOST_PORT': '28110', 'AUTH_HOST_PORT': '28111',
    'TRANSACTION_HOST_PORT': '28112', 'PAYMENT_HOST_PORT': '28113',
    'ACCOUNT_HOST_PORT': '28114', 'LEDGER_HOST_PORT': '28115',
    'POSTGRES_HOST_PORT': '25434',
}
SERVICES = ('postgres', 'auth-service', 'account-service', 'transaction-service',
            'payment-service', 'ledger-service', 'gateway')


def override_text() -> str:
    # No shared fault state or demo-orchestration writes between stacks.
    services = ['auth-service', 'account-service', 'transaction-service',
                'payment-service', 'ledger-service', 'gateway']
    lines = ['services:']
    for service in services:
        lines.extend([f'  {service}:', '    volumes:',
                      '      - ./data/v2-isolated-db/fault-control:/faultweave-control:ro'
                      if service != 'gateway' else
                      '      - ./data/v2-isolated-db/fault-control:/faultweave-control'])
        if service == 'gateway':
            lines.extend(['      - ./data/v2-isolated-db/demo-orchestration:/faultweave-demo',
                          '      - ./data/datasets/final/models:/faultweave-models:ro'])
    return '\n'.join(lines) + '\n'


def compose_args() -> list[str]:
    return ['docker', 'compose', '-p', PROJECT, '-f', 'docker-compose.yml',
            '-f', str(OVERRIDE)]


def environment() -> dict[str, str]:
    return {**os.environ, **PORTS}


def command(*tail: str, timeout: int = 180) -> str:
    if not OVERRIDE.is_file() or OVERRIDE.read_text(encoding='utf-8') != override_text():
        raise RuntimeError('STOP: isolated Compose override missing or changed')
    p = subprocess.run([*compose_args(), *tail], cwd=ROOT,
                       env=environment(), text=True, capture_output=True,
                       timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError('STOP: isolated Docker command failed: ' +
                           (p.stderr or p.stdout).strip()[:600])
    return p.stdout.strip()


def assert_free_ports() -> None:
    for port in map(int, PORTS.values()):
        sock = socket.socket()
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'STOP: isolated host port {port} is occupied') from exc
        finally:
            sock.close()


def validate_config() -> dict:
    config = json.loads(command('config', '--format', 'json', timeout=30))
    if config.get('name') != PROJECT:
        raise RuntimeError('STOP: Compose project name unexpectedly matches primary')
    svcs = config.get('services') or {}
    if set(svcs) != set(SERVICES):
        raise RuntimeError('STOP: unexpected isolated service count')
    pg = svcs['postgres']
    if pg.get('image') != 'postgres:17-alpine':
        raise RuntimeError('STOP: isolated PostgreSQL image changed')
    volumes = pg.get('volumes', [])
    if not any(v.get('type') == 'volume' and v.get('target') == '/var/lib/postgresql/data'
               for v in volumes if isinstance(v, dict)):
        raise RuntimeError('STOP: missing isolated persistent PostgreSQL volume')
    for svc in SERVICES[1:]:
        env = svcs[svc].get('environment') or {}
        database_url = env.get('DATABASE_URL', '')
        if not isinstance(database_url, str) or not re.search(
                r'@postgres:5432/faultweave(?:\?|$)', database_url):
            raise RuntimeError(f'STOP: {svc} does not point to internal isolated database')
        if env.get('APP_ENV') != 'development':
            raise RuntimeError(f'STOP: {svc} APP_ENV must be development')
        mounts = svcs[svc].get('volumes', [])
        mount = next((v for v in mounts if v.get('target') == '/faultweave-control'), None)
        if not mount or 'v2-isolated-db' not in str(mount.get('source')):
            raise RuntimeError(f'STOP: {svc} shares original fault controls')
    return config


def prepare() -> None:
    if OVERRIDE.exists() or OVERRIDE.is_symlink():
        raise RuntimeError('STOP: fixture already exists, do not overwrite')
    if not (ROOT / 'docker-compose.yml').is_file():
        raise RuntimeError('STOP: expected project docker-compose.yml missing')
    assert_free_ports()
    for rel in ('fault-control', 'demo-orchestration'):
        folder = FIXTURE / rel
        if folder.exists() or folder.is_symlink():
            raise RuntimeError('STOP: existing isolated fixture path: ' + str(folder))
    FIXTURE.mkdir(parents=True, exist_ok=True)
    for rel in ('fault-control', 'demo-orchestration'):
        (FIXTURE / rel).mkdir()
    OVERRIDE.write_text(override_text(), encoding='utf-8')
    validate_config()
    print('PREPARED isolated Compose override; original stack and database untouched')
    print('Fixture:', OVERRIDE)


def inspect_healthy() -> dict[str, str]:
    validate_config()
    ids: dict[str, str] = {}
    for svc in SERVICES:
        ident = command('ps', '-q', svc, timeout=30).splitlines()
        if len(ident) != 1 or not re.fullmatch(r'[a-f0-9]{12,64}', ident[0]):
            raise RuntimeError(f'STOP: isolated {svc} container missing or ambiguous')
        result = subprocess.run(['docker', 'inspect', '--format',
                                 '{{.State.Health.Status}}', ident[0]],
                                text=True, capture_output=True, timeout=20, check=False)
        if result.returncode != 0 or result.stdout.strip() != 'healthy':
            raise RuntimeError(f'STOP: isolated {svc} not healthy')
        label = subprocess.run(['docker', 'inspect', '--format',
                                '{{index .Config.Labels "com.docker.compose.project"}}', ident[0]],
                               text=True, capture_output=True, timeout=20, check=False)
        if label.returncode != 0 or label.stdout.strip() != PROJECT:
            raise RuntimeError(f'STOP: isolated {svc} project label mismatch')
        ids[svc] = ident[0]
    primary = subprocess.run(['docker', 'compose', 'ps', '-q', 'postgres'],
                             cwd=ROOT, text=True, capture_output=True, timeout=30, check=False)
    if primary.returncode != 0 or not primary.stdout.strip() or primary.stdout.strip() == ids['postgres']:
        raise RuntimeError('STOP: primary and isolated PostgreSQL identity not proven distinct')
    return ids


def smoke_transaction() -> None:
    """One benign transaction against the isolated gateway only; no training data."""
    inspect_healthy()
    import httpx
    run_id = 'v2isodb-smoke-' + uuid4().hex[:10]
    payload = {
        'username': 'demo', 'password': 'faultweave-demo',
        'account_number': 'FW-DEMO-001', 'amount_minor': 1000,
        'currency': 'INR', 'recipient': f'isolated-fixture-check-{run_id}',
    }
    headers = {'X-Run-ID': run_id, 'X-Request-ID': run_id+'-request-0001',
               'X-Trace-ID': str(uuid4())}
    with httpx.Client(timeout=15) as client:
        response = client.post('http://127.0.0.1:28110/api/v1/transactions',
                               headers=headers, json=payload)
    if response.status_code != 200 or response.json().get('status') != 'COMPLETED':
        raise RuntimeError('STOP: isolated gateway did not complete benign transaction')
    print('ISOLATED STACK SMOKE PASS: one successful isolated-only transaction')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    x = p.add_mutually_exclusive_group(required=True)
    x.add_argument('--preview', action='store_true')
    x.add_argument('--prepare', action='store_true')
    x.add_argument('--start', action='store_true')
    x.add_argument('--verify', action='store_true')
    x.add_argument('--smoke', action='store_true')
    p.add_argument('--acknowledge-separate-seven-container-stack', action='store_true')
    args = p.parse_args(argv)
    if args.preview:
        print('READ ONLY: isolated Docker Compose project', PROJECT)
        print('UNUSED PORTS REQUIRED:', ', '.join(PORTS.values()))
        print('Separate Postgres volume, fault-control and demo-orchestration')
        print('No production database stop or deletion supported')
        print(override_text())
    elif args.prepare:
        prepare()
    elif args.start:
        if not args.acknowledge_separate_seven_container_stack:
            raise RuntimeError('STOP: seven-container isolated-stack acknowledgment required')
        assert_free_ports()
        validate_config()
        # The command only creates/starts the dedicated isolated project.
        command('up', '-d', '--build', '--wait', timeout=450)
        inspect_healthy()
        print('ISOLATED STACK HEALTHY at http://127.0.0.1:28110')
    elif args.smoke:
        smoke_transaction()
    else:
        inspect_healthy()
        print('ISOLATED STACK VERIFIED; original PostgreSQL remains a separate container')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('ISOLATED DATABASE FIXTURE STOP:', exc)
        raise SystemExit(2)
