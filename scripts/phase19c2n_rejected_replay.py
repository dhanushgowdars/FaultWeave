#!/usr/bin/env python3
"""Read-only, hash-verified schema replay on one *rejected* resource experiment.

This is diagnostic analysis. A rejected experiment remains rejected and cannot
be promoted to training, nor can this tool release any supervisor reservation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts.phase19c2n_resource_pilot import is_observable_service_failure

RUN_RE = re.compile(r'v2resource-[0-9a-f]{16}\Z')
NAMES = ('requests.jsonl', 'recovery_requests.jsonl', 'events.jsonl', 'database_samples.jsonl')


def loaded(path):
    with path.open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def timestamp(s):
    if not isinstance(s,str): raise ValueError('Missing timestamp')
    return datetime.fromisoformat(s.replace('Z','+00:00'))


def analyze(root:Path, run_id:str):
    if not RUN_RE.fullmatch(run_id): raise ValueError('Invalid resource run identity')
    folder=root/'data/datasets/v2/rejected-resource'/run_id
    if folder.is_symlink() or not folder.is_dir(): raise ValueError('Missing rejected evidence')
    manpath=folder/'manifest.json'
    if manpath.is_symlink(): raise ValueError('Unsafe manifest path')
    man=json.loads(manpath.read_text(encoding='utf-8'))
    if man.get('run_id')!=run_id or man.get('verdict')!='REJECTED_RESOURCE_EFFECT_GATE' or man.get('training_eligible') is not False:
        raise ValueError('Not a rejected resource experiment')
    inv=man.get('artifacts')
    if not isinstance(inv,dict) or set(inv)!=set(NAMES): raise ValueError('Invalid artifact inventory')
    entries=[]
    for name in NAMES:
        path=folder/name
        if path.is_symlink() or not path.is_file(): raise ValueError('Unsafe artifact path')
        raw=path.read_bytes()
        if inv[name].get('bytes')!=len(raw) or inv[name].get('sha256')!=hashlib.sha256(raw).hexdigest():
            raise ValueError('Artifact checksum mismatch: '+name)
        entries.append(loaded(path))
    requests, recovery, events, samples=entries
    start=timestamp(man['window_started_at']); end=timestamp(man['window_ended_at'])
    if (end-start).total_seconds()!=60: raise ValueError('Not a 60-second window')
    inwindow={r['request_id']:r for r in requests if start<timestamp(r['started_at'])<=end}
    failed={rid for rid,r in inwindow.items() if r.get('transport_error') is not None
            or type(r.get('status_code')) is not int or r['status_code']>=500}
    same=[e for e in events if e.get('request_id') in failed and e.get('trace_id')==inwindow[e['request_id']].get('trace_id')
          and e.get('service') in ('gateway','transaction','payment') and start<timestamp(e['timestamp'])<=end]
    observed={e['request_id'] for e in same if is_observable_service_failure(e)}
    physical=[s for s in samples if start<timestamp(s['timestamp'])<=end]
    peak=max((s['total_connections'] for s in physical),default=None)
    waiters=max((s['advisory_waiters'] for s in physical),default=None)
    success=sum(r.get('status_code')==200 and r.get('transport_error') is None for r in recovery)
    print('READ-ONLY CORRECTED-SCHEMA EVIDENCE REPLAY:',run_id)
    print('4/4 SHA256 verified, exact 60s; ORIGINAL VERDICT REMAINS REJECTED')
    print('In-window failed clients:',len(failed), 'same-run correlated server failures:',len(observed))
    print('Physical in-window samples:',len(physical),'DB connection peak:',peak,'advisory-waiter peak:',waiters)
    print('Post-clear HTTP 200 recovery:',success,'of',len(recovery))
    print('Do not infer connection pressure without the recorded preflight baseline.')
    print('NO TRAINING ADMISSION / NO FILE CHANGES / NO FAULT / NO LOCK ACTION')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id', default='v2resource-e45a8dd275a84466')
    a=ap.parse_args()
    try: analyze(Path.cwd(),a.run_id)
    except (OSError,RuntimeError,ValueError,KeyError,TypeError) as exc:
        print('REPLAY STOP:',exc,file=sys.stderr);return 2
    return 0

if __name__=='__main__': raise SystemExit(main())
