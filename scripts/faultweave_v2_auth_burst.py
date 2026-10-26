#!/usr/bin/env python3
"""Bounded high-frequency invalid-login capture with precommitted 60s evidence.

No password spraying against external services: all requests go to the fixed local
FaultWeave gateway, use the project's intentionally invalid demo credential,
and carry a run-specific X-Run-ID. Do not relabel the old 1.5RPS auth diagnostic.

5 RPS is an explicitly defined ~3.33x burst relative to the tested 1.5RPS
baseline, not proof of the original 20-60RPS experimental band.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
import json
import subprocess
import sys

from scripts import faultweave_v2_capture as base
from scripts import faultweave_v2_verified_capture as five

LABEL = 'AUTHENTICATION_FAILURE_BURST'
TARGET = 'authentication'
INTENSITY = 'mild'
RPS = 5.0
DURATION = 75
MIN_OBSERVED_RPS = 4.50
MIN_AUTH_FAILURE_RATIO = 0.90
SCHEMA_COLLECTOR = 'auth-burst-5rps.1'


def split_for(slot: int) -> str:
    if type(slot) is not int or not 1 <= slot <= 12:
        raise ValueError('STOP: authentication training slot must be 1..12')
    return 'train' if slot <= 8 else 'validation' if slot <= 10 else 'test'


def directory_for(slot: int) -> Path:
    return base.run_dir(LABEL, slot)


def effect_gate(window, requests, events) -> dict:
    """Judge observable 401 frequency, not the injected fault label."""
    from scripts.phase19c2d_fault_pilot import parsed_time
    window_rows = {r['request_id']: r for r in requests
                   if window.started_at < parsed_time(r['started_at']) <= window.ended_at}
    rate = len(window_rows)/60.0
    if rate < MIN_OBSERVED_RPS:
        raise RuntimeError(f'STOP: AUTH_BURST_NOT_PROVEN measured={rate:.3f} < {MIN_OBSERVED_RPS:.2f} RPS')
    rejections = {rid for rid, r in window_rows.items()
                  if r.get('scenario') == 'invalid_login' and r.get('status_code') == 401
                  and r.get('expected_outcome') is True and r.get('transport_error') is None}
    if len(rejections) < MIN_AUTH_FAILURE_RATIO*len(window_rows):
        raise RuntimeError('STOP: no sustained high-frequency authentication rejection pattern')
    auth_correlated = {e.get('request_id') for e in events
                       if e.get('service') == 'authentication'
                       and e.get('event_type') == 'http_request_completed'
                       and e.get('status_code') == 401
                       and e.get('request_id') in rejections
                       and e.get('trace_id') == window_rows[e['request_id']]['trace_id']
                       and window.started_at < parsed_time(e['timestamp']) <= window.ended_at}
    if len(auth_correlated) < 0.75*len(rejections):
        raise RuntimeError('STOP: insufficient trace-matched authentication 401 evidence')
    return {'in_window_requests': len(window_rows), 'observed_rps': round(rate, 5),
            'client_401_rejections': len(rejections),
            'correlated_authentication_401_requests': len(auth_correlated),
            'baseline_rps': base.RPS,
            'relative_to_baseline': round(rate/base.RPS, 5),
            'burst_contract': '>=4.5 observed RPS and >=90% client 401 with >=75% auth trace corroboration',
            'original_20_60_rps_band_proven': False}


def make_intent(slot: int, run_id: str) -> Path:
    split = split_for(slot)
    base.clean_lock()
    base.ensure_compact_parent()
    directory = directory_for(slot)
    if directory.exists() or directory.is_symlink():
        raise RuntimeError('STOP: this authentication slot was previously attempted')
    base.RUNS.mkdir(parents=True, exist_ok=True)
    base.LOCK.parent.mkdir(parents=True, exist_ok=True)
    if base.RUNS.is_symlink() or base.LOCK.parent.is_symlink():
        raise RuntimeError('STOP: training path is symlink')
    base.LOCK.mkdir(exist_ok=False)
    base.write_new(base.LOCK/'owner.json', base.serial({'run_id':run_id,'slot':slot,'label':LABEL}))
    directory.mkdir(exist_ok=False)
    intent = {'schema_version': 'faultweave-v2-training-intent.1',
              'purpose': 'new independent training capture, NOT historical/diagnostic promotion',
              'run_id':run_id, 'label':LABEL, 'slot':slot, 'split':split,
              'scenario_type':'known_fault', 'target':TARGET,'intensity':INTENSITY,
              'rps':RPS, 'duration_seconds':DURATION,
              'collection_started_at':datetime.now(UTC).isoformat(),
              'collector':SCHEMA_COLLECTOR,'mechanism':'auth-rejection-burst',
              'original_20_60_rps_band_proven': False}
    base.write_new(directory/'intent.json', base.serial(intent))
    return directory


async def observe(run_id: str) -> dict:
    from scripts.phase19c2c_healthy_pilot import require_clean_local_stack, visible_events, proven_client_rows
    from scripts.phase19c2d_fault_pilot import new_plan, correlation_gate, recovery_gate, parsed_time
    from scripts.phase19c2l_auth_pilot import execute_auth_phase, auth_rows
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from experiments.faults.lifecycle import FaultStateStore
    from experiments.runner import collect_run_events, execute_plan
    require_clean_local_stack()
    plan = [replace(p, scenario='invalid_login') for p in new_plan(run_id, RPS)]
    if len(plan) != round(DURATION*RPS):
        raise RuntimeError('STOP: invalid login plan size not at admitted 5RPS contract')
    url='http://127.0.0.1:18110'
    store=FaultStateStore(base.ROOT/'data/fault-control')
    raw, started, ended, activation, cleared = await execute_auth_phase(store, run_id, plan, url, execute_plan)
    require_clean_local_stack()
    # The burst uses 375 IDs (1..375). Recovery MUST start at 376, not 113.
    # Keep the 1.5 RPS recovery offsets while avoiding request-ID collisions.
    recovery_plan = [replace(item, sequence=len(plan) + i)
                     for i, item in enumerate(new_plan(run_id, base.RPS, recovery=True), start=1)]
    if len({item.sequence for item in plan + recovery_plan}) != len(plan) + len(recovery_plan):
        raise RuntimeError('STOP: duplicate client IDs in burst/recovery plan')
    recovered_raw = await execute_plan(recovery_plan, url, run_id, 1951, 8)
    require_clean_local_stack()
    requests, recovery = auth_rows(raw, run_id), proven_client_rows(recovered_raw, run_id)
    events=visible_events(collect_run_events(run_id),run_id)
    correlation_gate(requests+recovery,events)
    recovery_proof=recovery_gate(recovery,events,cleared_at=cleared)
    watermark=max(parsed_time(event['timestamp']) for event in events)
    window=select_exact_window(phase_started_at=started, phase_ended_at=ended,
                               telemetry_watermark=watermark,
                               fault_activated_at=activation.activated_at,
                               fault_expires_at=activation.expires_at,
                               fault_cleared_at=cleared, fault_required=True,
                               lead_guard_seconds=2,tail_guard_seconds=2)
    effect=effect_gate(window,requests,events)
    feature=build_observable_features(run_id=run_id,window=window,requests=requests,
                                      events=events,require_boundary_evidence=True)
    metadata={'phase_started_at':started.isoformat(),'phase_ended_at':ended.isoformat(),
              'telemetry_watermark':watermark.isoformat(),
              'fault_activated_at':parsed_time(activation.activated_at).isoformat(),
              'fault_expires_at':parsed_time(activation.expires_at).isoformat(),
              'fault_cleared_at':cleared.isoformat(),'healthy_pre_post':True,
              'scheduled_requests':len(plan),'scheduled_rps':RPS,
              'impact':effect,'recovery':recovery_proof,'mechanism':'auth-rejection-burst'}
    truth={'run_id':run_id,'fault_id':LABEL,'target':TARGET,'intensity':INTENSITY,
           'activated_at':parsed_time(activation.activated_at).isoformat(),
           'expires_at':parsed_time(activation.expires_at).isoformat(),
           'cleared_at':cleared.isoformat(),'truth_role':'audit_only_not_model_feature'}
    return dict(requests=requests,recovery=recovery,events=events,window=window,
                vector=feature,metadata=metadata,truth=truth,db_samples=None)


async def capture(slot: int):
    split_for(slot)
    if directory_for(slot).exists() or directory_for(slot).is_symlink():
        raise RuntimeError('STOP: auth slot already attempted')
    base.preflight()
    run_id='v2train-'+uuid4().hex[:16]
    folder=make_intent(slot,run_id)
    try:
        data=await observe(run_id)
        five.emit(folder,run_id,LABEL,split_for(slot),**data)
        base.release_own_lock(run_id)
    except BaseException:
        print('STOP: preserve attempt and reservation; no automatic retry',flush=True)
        raise
    return folder


def run_batch(start: int,count: int):
    if count<1 or count>10 or start<1 or start+count-1>12:
        raise ValueError('STOP: batch must be <=10 unused slots within 1..12')
    for slot in range(start,start+count):
        print('BEGIN',LABEL,'SLOT',slot,flush=True)
        asyncio.run(capture(slot))
        for command in ('--check','--admit'):
            rc=subprocess.run([sys.executable,'-m','scripts.faultweave_v2_auth_burst_audit',
                               command,'--slot',str(slot)],cwd=base.ROOT).returncode
            if rc:
                raise RuntimeError(f'STOP: auth independent {command} failed for slot {slot}')
        print('ADMITTED',LABEL,'SLOT',slot,flush=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--preview',action='store_true');g.add_argument('--batch',action='store_true')
    p.add_argument('--start-slot',type=int,default=1);p.add_argument('--count',type=int,default=1)
    p.add_argument('--acknowledge-bounded-auth-burst',action='store_true')
    a=p.parse_args(argv)
    split_for(a.start_slot);split_for(a.start_slot+a.count-1)
    if a.count<1 or a.count>10: raise ValueError('STOP: invalid batch count')
    if a.preview:
        print(f'PREVIEW {LABEL}: local gateway, 5RPS invalid_login, 75s, '
              f'60s exact, slots={a.start_slot}..{a.start_slot+a.count-1}; NO CHANGES')
        print('Relative rejection burst >=3x 1.5RPS baseline, NOT evidence of original 20-60RPS band')
        return
    if not a.acknowledge_bounded_auth_burst:
        raise ValueError('STOP: explicit bounded local authentication burst acknowledgment required')
    run_batch(a.start_slot,a.count)

if __name__=='__main__':
    try: main()
    except (RuntimeError,ValueError,OSError) as exc:
        print('V2 AUTH BURST STOP:',exc,file=sys.stderr);raise SystemExit(2)
