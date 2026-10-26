#!/usr/bin/env python3
"""Phase 19C.2K: fail-closed *diagnostic* collection supervisor.

The 240-run Model v2 proposal is immutable and wholly blocked. This controller
cannot execute a proposed row, admit training data, or authorize new fault
families. It can preview rows and explicitly run ONLY existing, bounded diagnostic
collectors, with an exclusive attempt journal and post-run independent audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase19c2i_readiness import PROPOSAL, audit as readiness_audit, strict_json  # noqa: E402

SCHEMA = '19c2k.diagnostic-supervisor.1'
CONTROL = Path('data/datasets/v2/control/phase19c2k')
LOCK = Path('data/datasets/v2/control/phase19c2k-active')
ATTEMPT_RE = re.compile(r'[a-z0-9][a-z0-9_-]{3,59}\Z')
RUN_RE = {
    'healthy': re.compile(r'v2pilot-[0-9a-f]{16}\Z'),
    'payment-error': re.compile(r'v2fault-[0-9a-f]{16}\Z'),
    'timeout': re.compile(r'v2timeout-[0-9a-f]{16}\Z'),
    'database-latency': re.compile(r'v2effect-[0-9a-f]{16}\Z'),
    'service-unavailable': re.compile(r'v2effect-[0-9a-f]{16}\Z'),
    'auth-rejection': re.compile(r'v2auth-[0-9a-f]{16}\Z'),
    'connection-pool': re.compile(r'v2resource-[0-9a-f]{16}\Z'),
    'database-lock': re.compile(r'v2resource-[0-9a-f]{16}\Z'),
    'database-unavailable': re.compile(r'v2finaltwo-[0-9a-f]{16}\Z'),
    'high-load': re.compile(r'v2finaltwo-[0-9a-f]{16}\Z'),
}


@dataclass(frozen=True)
class Adapter:
    kind: str
    module: str
    relative_dir: str
    target: str | None
    rps: float
    fault: str | None = None

    @property
    def prefix(self) -> str:
        return {'healthy': 'v2pilot-', 'payment-error': 'v2fault-', 'timeout': 'v2timeout-',
                'database-latency': 'v2effect-', 'service-unavailable': 'v2effect-',
                'auth-rejection': 'v2auth-', 'connection-pool': 'v2resource-',
                'database-lock': 'v2resource-',
                'database-unavailable': 'v2finaltwo-',
                'high-load': 'v2finaltwo-'}[self.kind]

    def command(self, *, execute: bool) -> list[str]:
        cmd = [sys.executable, '-m', self.module,
               '--execute' if execute else '--dry-run', '--rps', str(self.rps),
               '--gateway-url', 'http://127.0.0.1:18110']
        if self.target is not None and self.kind == 'timeout':
            cmd.extend(['--target', self.target])
        elif self.kind in ('database-latency', 'service-unavailable',
                             'connection-pool', 'database-lock'):
            cmd.extend(['--fault', self.fault, '--target', self.target])
            if execute:
                cmd.append('--acknowledge-diagnostic-only')
        if self.kind in ('database-unavailable', 'high-load'):
            cmd.extend(['--fault', self.fault, '--target', self.target])
            if execute:
                cmd.append('--acknowledge-diagnostic-only')
                if self.kind == 'database-unavailable':
                    cmd.append('--acknowledge-local-db-outage')
        return cmd


def choose_adapter(kind: str, target: str | None, rps: float) -> Adapter:
    """Only configurations implemented and previously exercised by current collectors.

    The helper deliberately is NOT the matrix executor. It creates new UUID
    diagnostic pilots with no training split or planned run ID.
    """
    if not isinstance(rps, (float, int)) or isinstance(rps, bool) or not math.isfinite(rps):
        raise ValueError('finite diagnostic RPS required')
    if kind == 'high-load':
        from scripts.phase19c_last_two_pilot import validate_configuration
        validate_configuration('HIGH_LOAD', target, rps)
        return Adapter(kind, 'scripts.phase19c_last_two_pilot',
                       'data/datasets/v2/pilot-fault', target, float(rps), fault='HIGH_LOAD')
    if kind == 'database-unavailable':
        from scripts.phase19c_last_two_pilot import validate_configuration
        validate_configuration('DATABASE_UNAVAILABLE', target, rps)
        return Adapter(kind, 'scripts.phase19c_last_two_pilot',
                       'data/datasets/v2/pilot-fault', target, float(rps), fault='DATABASE_UNAVAILABLE')
    if float(rps) not in (1.5, 2.0):
        raise ValueError('only previously tested 1.5 or 2.0 RPS diagnostic settings allowed')
    if kind == 'auth-rejection':
        if target != 'authentication' or rps != 1.5:
            raise ValueError('Only 1.5 RPS proven invalid-login diagnostic is supported')
        return Adapter(kind, 'scripts.phase19c2l_auth_pilot',
                       'data/datasets/v2/pilot-fault', 'authentication', 1.5,
                       fault='AUTHENTICATION_FAILURE_BURST')
    if kind in ('database-latency', 'service-unavailable'):
        from scripts.phase19c2m_service_effect_pilot import allowed_configuration
        fault = ('DATABASE_HIGH_LATENCY' if kind == 'database-latency'
                 else 'SERVICE_UNAVAILABLE')
        allowed_configuration(fault, target, rps)
        return Adapter(kind, 'scripts.phase19c2m_service_effect_pilot',
                       'data/datasets/v2/pilot-fault', target, float(rps), fault=fault)
    if kind in ('connection-pool', 'database-lock'):
        from scripts.phase19c2n_resource_pilot import configuration
        fault = ('CONNECTION_POOL_EXHAUSTION' if kind == 'connection-pool'
                 else 'DATABASE_LOCK_CONTENTION')
        configuration(fault, target, rps)
        return Adapter(kind, 'scripts.phase19c2n_resource_pilot',
                       'data/datasets/v2/pilot-fault', target, float(rps), fault=fault)
    if kind == 'healthy':
        if target is not None:
            raise ValueError('healthy diagnostic cannot have a fault target')
        return Adapter(kind, 'scripts.phase19c2c_healthy_pilot',
                       'data/datasets/v2/pilot', None, float(rps))
    if kind == 'payment-error':
        if target != 'payment':
            raise ValueError('payment-error diagnostic requires exact target payment')
        return Adapter(kind, 'scripts.phase19c2d_fault_pilot',
                       'data/datasets/v2/pilot-fault', 'payment', float(rps))
    if kind == 'timeout':
        if target not in ('payment->ledger', 'transaction->payment'):
            raise ValueError('timeout diagnostic target must be a previously verified edge')
        return Adapter(kind, 'scripts.phase19c2j_timeout_pilot',
                       'data/datasets/v2/pilot-fault', target, float(rps))
    raise ValueError('unsupported diagnostic kind; seven additional fault families remain blocked')


def verify_blocked_project(root: Path) -> dict[str, Any]:
    """Reproduce the full 19C.2I offline audit, then recheck zero permissions."""
    report = readiness_audit(root)
    proposal = strict_json(root / PROPOSAL)
    rows = proposal.get('runs')
    if (report.get('status') != 'NO_GO_BULK_COLLECTION' or
        report.get('plan_sha256') != proposal.get('draft_sha256') or
        report.get('plan', {}).get('blocked') != 240 or
        not isinstance(rows, list) or len(rows) != 240 or
        any(not isinstance(r, dict) or r.get('execution_allowed') is not False or
            r.get('training_eligible') is not False or r.get('target_rps') is not None for r in rows) or
        any(v is not False for v in proposal.get('approval', {}).values()) or
        report.get('approval', {}).get('bulk_execution') is not False or
        report.get('approval', {}).get('training_eligibility') is not False):
        raise RuntimeError('STOP: v2 matrix/readiness not exactly the verified blocked contract')
    return {'proposal_sha256': proposal['draft_sha256'], 'runs': rows, 'report': report}


def planned_row(contract: dict, run_id: str) -> dict[str, Any]:
    rows = [r for r in contract['runs'] if r.get('run_id') == run_id]
    if len(rows) != 1:
        raise ValueError('Unknown or ambiguous planned run ID')
    row = rows[0]
    if row['execution_allowed'] is not False:
        raise RuntimeError('Unexpected planned execution approval')
    return row


def attempt_paths(root: Path, attempt_id: str) -> tuple[Path, Path]:
    if not isinstance(attempt_id, str) or not ATTEMPT_RE.fullmatch(attempt_id):
        raise ValueError('attempt ID must be 4–60 lowercase letters/digits/_/-; never use plan run IDs')
    if attempt_id.startswith(('v2-', 'v2pilot-', 'v2fault-', 'v2timeout-')):
        raise ValueError('attempt ID must not resemble a planned or generated run ID')
    return root / CONTROL / attempt_id, root / LOCK


def stable_json(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def create_json(path: Path, obj: dict) -> None:
    with path.open('xb') as file:
        file.write(stable_json(obj))


def existing_pilots(root: Path, adapter: Adapter) -> set[str]:
    location = root / adapter.relative_dir
    if not location.exists():
        return set()
    if location.is_symlink() or not location.is_dir():
        raise RuntimeError('STOP: pilot output root is not a real directory')
    found = set()
    for p in location.iterdir():
        if p.name.startswith(adapter.prefix):
            if p.is_symlink() or not p.is_dir() or not RUN_RE[adapter.kind].fullmatch(p.name):
                raise RuntimeError(f'STOP: suspicious diagnostic output entry {p.name}')
            found.add(p.name)
    return found


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open('r', encoding='utf-8') as fd:
        for line in fd:
            if line.strip():
                obj = json.loads(line, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                if not isinstance(obj, dict):
                    raise ValueError('nonobject JSONL record')
                rows.append(obj)
    return rows


def audit_healthy(root: Path, pilot: Path) -> None:
    """Independent exact-window / feature reproduction for new healthy pilots."""
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from scripts.phase19c2c_healthy_pilot import validate_correlations
    pilot = pilot.resolve(strict=True)
    if (pilot.parent != (root / 'data/datasets/v2/pilot').resolve(strict=True) or
        not RUN_RE['healthy'].fullmatch(pilot.name)):
        raise ValueError('Untrusted healthy pilot directory')
    manifest = strict_json(pilot / 'manifest.json')
    candidate = strict_json(pilot / 'candidate_feature.json')
    if (manifest.get('run_id') != pilot.name or candidate.get('run_id') != pilot.name or
        manifest.get('schema_version') != '19c2c.pilot.1' or
        candidate.get('schema_version') != '19c2c.pilot.1' or
        manifest.get('split') != 'diagnostic_only' or candidate.get('split') != 'diagnostic_only' or
        manifest.get('training_eligible') is not False or candidate.get('training_eligible') is not False or
        manifest.get('threshold_tuning_eligible') is not False or candidate.get('threshold_tuning_eligible') is not False or
        candidate.get('label') is not None or manifest.get('known_fault_label') is not None or
        candidate.get('candidate_status') != 'UNLABELED_HEALTHY_PILOT' or
        candidate.get('window_seconds') != 60 or candidate.get('feature_count') != 65 or
        manifest.get('outgoing_header') != 'X-Run-ID'):
        raise ValueError('Healthy diagnostic-only contract mismatch')
    names = {'requests.jsonl', 'events.jsonl', 'candidate_feature.json'}
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, dict) or set(artifacts) != names:
        raise ValueError('Healthy pilot artifact inventory mismatch')
    for name in names:
        raw = (pilot / name).read_bytes()
        item = artifacts[name]
        if item.get('sha256') != hashlib.sha256(raw).hexdigest() or item.get('bytes') != len(raw):
            raise ValueError(f'Healthy pilot checksum mismatch: {name}')
    expected_hash = hashlib.sha256('\n'.join(FEATURE_NAMES).encode()).hexdigest()
    if manifest.get('feature_schema_sha256') != expected_hash:
        raise ValueError('Healthy feature schema SHA mismatch')
    requests, events = _read_jsonl(pilot/'requests.jsonl'), _read_jsonl(pilot/'events.jsonl')
    if not requests or not events or any(r.get('run_id') != pilot.name or r.get('scenario') != 'valid' for r in requests) or any(e.get('run_id') != pilot.name for e in events):
        raise ValueError('Mixed-run or invalid healthy observations')
    allowed_requests = {'run_id','request_id','trace_id','started_at','latency_ms','status_code','expected_outcome','transport_error','scenario'}
    allowed_events = {'run_id','request_id','trace_id','timestamp','service','event_type','path','downstream_service','latency_ms','success','level','status_code','error_type'}
    if any(set(row) - allowed_requests for row in requests) or any(set(row) - allowed_events for row in events):
        raise ValueError('Unexpected observation fields')
    ids = [r.get('request_id') for r in requests]
    if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i.startswith(pilot.name+'-request-') for i in ids):
        raise ValueError('Unproven healthy request IDs')
    validate_correlations(requests, events)
    meta = manifest.get('metadata') or {}
    if (meta.get('passed_pre_and_post_run_healthy_no_fault') is not True or
        meta.get('observed_requests') != len(requests) or meta.get('observed_events') != len(events)):
        raise ValueError('No complete healthy stack checks')
    window = select_exact_window(phase_started_at=meta['phase_started_at'],
                                 phase_ended_at=meta['phase_ended_at'],
                                 telemetry_watermark=meta['telemetry_watermark'],
                                 fault_required=False, lead_guard_seconds=2.0,
                                 tail_guard_seconds=2.0)
    if (window.started_at.isoformat() != candidate.get('window_started_at') or
        window.ended_at.isoformat() != candidate.get('window_ended_at') or
        candidate.get('window_started_at') != manifest.get('window_started_at') or
        candidate.get('window_ended_at') != manifest.get('window_ended_at')):
        raise ValueError('Inconsistent healthy window')
    vector = build_observable_features(run_id=pilot.name, window=window, requests=requests,
                                       events=events, require_boundary_evidence=True)
    if candidate.get('features') != vector or tuple(vector) != FEATURE_NAMES:
        raise ValueError('Healthy 65-feature recomputation mismatch')
    print('HEALTHY AUDIT: 3/3 hashes; exact 60s; 65/65 features; diagnostic only')


def audit_pilot(root: Path, pilot: Path, adapter: Adapter) -> None:
    """Read-only independent auditor; no training admission."""
    if adapter.kind == 'healthy':
        audit_healthy(root, pilot)
    elif adapter.kind == 'payment-error':
        from scripts.phase19c2d_integrity_audit import audit
        if audit(root, pilot) != 0:
            raise RuntimeError('Payment-error auditor rejected pilot')
    elif adapter.kind == 'timeout':
        from scripts.phase19c2j_timeout_audit import audit
        if audit(root, pilot) != 0:
            raise RuntimeError('Timeout auditor rejected pilot')
        truth = strict_json(pilot/'protected_ground_truth.json')
        if truth.get('target') != adapter.target:
            raise RuntimeError('Timeout pilot target does not match requested diagnostic')
    elif adapter.kind == 'auth-rejection':
        from scripts.phase19c2l_auth_audit import audit
        if audit(root, pilot) != 0:
            raise RuntimeError('Auth-rejection auditor rejected pilot')
        truth = strict_json(pilot / 'protected_ground_truth.json')
        if truth.get('fault_id') != adapter.fault or truth.get('target') != adapter.target:
            raise RuntimeError('Audited auth-rejection target/family mismatch')
    elif adapter.kind in ('database-latency', 'service-unavailable'):
        from scripts.phase19c2m_service_effect_audit import audit
        if audit(root, pilot) != 0:
            raise RuntimeError('Service-effect auditor rejected pilot')
        truth = strict_json(pilot / 'protected_ground_truth.json')
        if truth.get('target') != adapter.target or truth.get('fault_id') != adapter.fault:
            raise RuntimeError('Audited service-effect target/family does not match adapter')
    elif adapter.kind in ('connection-pool', 'database-lock'):
        from scripts.phase19c2n_resource_audit import audit
        if audit(root, pilot) != 0:
            raise RuntimeError('Physical resource auditor rejected pilot')
        truth = strict_json(pilot / 'protected_ground_truth.json')
        if truth.get('target') != adapter.target or truth.get('fault_id') != adapter.fault:
            raise RuntimeError('Audited resource target/family does not match adapter')
    elif adapter.kind in ('database-unavailable','high-load'):
        from scripts.phase19c_last_two_audit import audit
        if audit(root, pilot) != 0:
            raise RuntimeError('Last-two independent auditor rejected pilot')
        truth = strict_json(pilot/'protected_ground_truth.json')
        if truth.get('target') != adapter.target or truth.get('fault_id') != adapter.fault:
            raise RuntimeError('Audited last-two family/target mismatch')
    else:
        raise ValueError('Unsupported audit adapter')


def run_diagnostic(root: Path, contract: dict, adapter: Adapter, attempt_id: str) -> Path:
    """Single attempt; fail closed. Never silently retry or erase evidence."""
    from scripts.phase19c2c_healthy_pilot import require_clean_local_stack
    attempt, lock = attempt_paths(root, attempt_id)
    if attempt.exists() or attempt.is_symlink():
        raise RuntimeError('STOP: attempt already exists; resume by inspecting evidence, not re-executing')
    if lock.exists() or lock.is_symlink():
        raise RuntimeError('STOP: another collection attempt or unresolved lock exists')
    require_clean_local_stack()
    before = existing_pilots(root, adapter)
    lock.parent.mkdir(parents=True, exist_ok=True)
    attempt.parent.mkdir(parents=True, exist_ok=True)
    lock.mkdir(exist_ok=False)  # exclusive process-level reservation, never steal it
    try:
        create_json(lock/'owner.json', {'schema_version': SCHEMA,'attempt_id':attempt_id,
                                        'kind':adapter.kind,'target':adapter.target})
        attempt.mkdir(exist_ok=False)
        create_json(attempt/'intent.json', {'schema_version': SCHEMA, 'attempt_id': attempt_id,
                   'kind':adapter.kind,'target':adapter.target,'rps':adapter.rps,
                   'proposal_sha256':contract['proposal_sha256'],
                   'split':'diagnostic_only','training_eligible':False,
                   'threshold_tuning_eligible':False,'planned_run_id':None,
                   'child_module':adapter.module})
        completed = subprocess.run(adapter.command(execute=True), cwd=root, check=False)
        if completed.returncode:
            raise RuntimeError(f'Diagnostic child exited {completed.returncode}; investigate before rerunning')
        require_clean_local_stack()
        after = existing_pilots(root, adapter)
        new = after - before
        if len(new) != 1 or before - after:
            raise RuntimeError(f'Expected exactly one new immutable {adapter.prefix} pilot; found {len(new)}')
        name = next(iter(new))
        folder = root / adapter.relative_dir / name
        audit_pilot(root, folder, adapter)
        digest = hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest()
        result = {'schema_version': SCHEMA, 'attempt_id': attempt_id,
                  'run_id': name, 'kind':adapter.kind,'target':adapter.target,'rps':adapter.rps,
                  'manifest_sha256':digest,'verdict':'VALID_DIAGNOSTIC_ONLY',
                  'split':'diagnostic_only','training_eligible':False,
                  'threshold_tuning_eligible':False,'planned_run_id':None}
        create_json(attempt/'result.json', result)
    except BaseException:
        # Preserve lock and intent. Any fault, telemetry or incomplete output must
        # be inspected manually. Automatic retry, rollback or lock theft is unsafe.
        print('SAFETY STOP: attempt journal/lock preserved; do not re-execute until inspected',
              file=sys.stderr)
        raise
    else:
        (lock/'owner.json').unlink()
        lock.rmdir()
        return folder


def status_attempts(root: Path) -> dict[str,int]:
    base = root / CONTROL
    states = {'completed':0, 'incomplete':0}
    if base.exists():
        for item in base.iterdir():
            if item.is_symlink() or not item.is_dir():
                raise RuntimeError('Unexpected attempt-journal entry')
            if (item/'result.json').is_file():
                states['completed'] += 1
            else:
                states['incomplete'] += 1
    return states


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--status', action='store_true', help='offline, read-only readiness and journal status')
    group.add_argument('--inspect-run', metavar='PLAN_RUN_ID', help='offline blocked-plan row preview')
    group.add_argument('--pilot', action='store_true', help='explicit diagnostic UUID pilot only')
    p.add_argument('--kind', choices=['healthy','payment-error','timeout',
                                     'database-latency','service-unavailable','auth-rejection',
                                     'connection-pool','database-lock',
                                     'database-unavailable','high-load'])
    p.add_argument('--target')
    p.add_argument('--rps', type=float)
    p.add_argument('--attempt-id')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--execute-pilot', action='store_true')
    p.add_argument('--acknowledge-diagnostic-only', action='store_true')
    p.add_argument('--acknowledge-local-db-outage', action='store_true')
    args = p.parse_args(argv)
    try:
        contract = verify_blocked_project(ROOT)
        if args.status or args.inspect_run:
            if any([args.kind,args.target,args.rps is not None,args.attempt_id,
                    args.dry_run,args.execute_pilot,args.acknowledge_diagnostic_only,
                    args.acknowledge_local_db_outage]):
                raise ValueError('Status/inspect modes do not accept pilot options')
            if args.status:
                states = status_attempts(ROOT)
                print('PHASE 19C.2K — UNIFIED DIAGNOSTIC COLLECTION SUPERVISOR')
                print('VERIFIED PROPOSAL:',contract['proposal_sha256'])
                print('PLANNED RUNS: 240/240 BLOCKED; 0 executable; 0 training eligible')
                print('ALLOWED PILOTS: healthy, payment-error, timeout, auth-rejection, database-latency, service-unavailable, connection-pool, database-lock, database-unavailable, high-load; diagnostic UUIDs only')
                print('JOURNALS:',states,'UNRESOLVED LOCK:',(ROOT/LOCK).exists())
                print('NO training/admission, Docker, traffic, or file writes')
            else:
                row = planned_row(contract,args.inspect_run)
                print(json.dumps(row, indent=2,sort_keys=True))
                print('VERDICT: BLOCKED PLAN ROW; cannot execute via this controller')
            return 0
        if args.pilot:
            if (args.kind is None or args.rps is None or
                args.dry_run == args.execute_pilot):
                raise ValueError('Pilot requires --kind, --rps, and exactly one of --dry-run/--execute-pilot')
            adapter = choose_adapter(args.kind,args.target,args.rps)
            if args.dry_run:
                if args.acknowledge_diagnostic_only or args.acknowledge_local_db_outage:
                    raise ValueError('Acknowledgement only applies to live execution')
                if args.attempt_id:
                    attempt_paths(ROOT,args.attempt_id)
                print('DRY RUN — one diagnostic collector; no Docker, network, artifacts, or fault activation')
                print('ADAPTER:',adapter.module,'TARGET:',adapter.target,'RPS:',adapter.rps)
                print('NEW UUID PILOT ONLY; no planned run ID, split, training, or model changes')
                return 0
            if not args.acknowledge_diagnostic_only or not args.attempt_id:
                raise ValueError('Live diagnostic requires --attempt-id and --acknowledge-diagnostic-only')
            if adapter.kind == 'database-unavailable' and not args.acknowledge_local_db_outage:
                raise ValueError('Physical PostgreSQL stop requires --acknowledge-local-db-outage')
            if adapter.kind != 'database-unavailable' and args.acknowledge_local_db_outage:
                raise ValueError('DB-outage acknowledgement is forbidden for any other fault')
            from scripts.phase19c2c_healthy_pilot import safe_url
            safe_url('http://127.0.0.1:18110')
            folder=run_diagnostic(ROOT,contract,adapter,args.attempt_id)
            print('VERIFIED DIAGNOSTIC OUTPUT:',folder)
            print('NOT TRAINING DATA; all 240 proposed rows remain blocked')
            return 0
        raise ValueError('No recognized action')
    except (ValueError,RuntimeError,OSError,KeyError,TypeError,ImportError) as exc:
        print('PHASE 19C.2K STOP:',exc,file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
