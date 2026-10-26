#!/usr/bin/env python3
"""Independent provenance, burst-effect, and 65-feature auditor for fresh auth runs."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from scripts import faultweave_v2_capture as base
from scripts import faultweave_v2_independent_audit as source
from scripts import faultweave_v2_auth_burst as capture


def audit(slot: int, *, write: bool=False) -> dict:
    split=capture.split_for(slot)
    directory=capture.directory_for(slot)
    if (not directory.is_dir() or directory.is_symlink() or
            directory.resolve().parent != base.RUNS.resolve()):
        raise ValueError('STOP: missing or symlink auth capture directory')
    from datasets.final_features import FEATURE_NAMES
    from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features
    from scripts.phase19c2d_fault_pilot import parsed_time, correlation_gate, recovery_gate

    intent_path, manifest_path=directory/'intent.json',directory/'source_manifest.json'
    intent, manifest=source.read_json(intent_path),source.read_json(manifest_path)
    run_id=intent.get('run_id')
    if (not isinstance(run_id,str) or len(run_id)!=24 or not run_id.startswith('v2train-')
            or intent.get('schema_version')!='faultweave-v2-training-intent.1'
            or intent.get('purpose')!='new independent training capture, NOT historical/diagnostic promotion'
            or intent.get('collector')!=capture.SCHEMA_COLLECTOR
            or intent.get('mechanism')!='auth-rejection-burst'
            or intent.get('original_20_60_rps_band_proven') is not False
            or intent.get('label')!=capture.LABEL or intent.get('slot')!=slot
            or intent.get('split')!=split or intent.get('scenario_type')!='known_fault'
            or intent.get('target')!=capture.TARGET or intent.get('intensity')!=capture.INTENSITY
            or intent.get('rps')!=capture.RPS or intent.get('duration_seconds')!=capture.DURATION):
        raise ValueError('STOP: precommitted auth burst experiment contract mismatch')
    if (manifest.get('schema_version')!='faultweave-v2-training-capture.1'
            or manifest.get('run_id')!=run_id or manifest.get('label')!=capture.LABEL
            or manifest.get('split')!=split or manifest.get('scenario_type')!='known_fault'
            or manifest.get('training_eligible') is not True
            or manifest.get('threshold_tuning_eligible') is not (split=='validation')
            or manifest.get('window_seconds')!=60
            or manifest.get('outgoing_header')!='X-Run-ID'
            or manifest.get('client_provenance')!='experiments.runner.execute_plan'
            or manifest.get('admission_requires_separate_independent_audit') is not True
            or manifest.get('intent_sha256')!=base.digest(intent_path.read_bytes())
            or manifest.get('feature_schema_sha256')!=base.digest('\n'.join(FEATURE_NAMES).encode())):
        raise ValueError('STOP: auth burst manifest mismatch')
    expected={'requests.jsonl','recovery_requests.jsonl','events.jsonl','feature_row.json',
              'capture_metadata.json','protected_ground_truth.json'}
    artifacts=manifest.get('artifacts')
    if not isinstance(artifacts,dict) or set(artifacts)!=expected:
        raise ValueError('STOP: missing/unexpected auth observation files')
    for name,entry in artifacts.items():
        if not isinstance(entry,dict) or type(entry.get('bytes')) is not int or not isinstance(entry.get('sha256'),str):
            raise ValueError('STOP: malformed recorded file digest')
        source.check_sha(directory/name,entry['sha256'],entry['bytes'])
    req=source.jsonl(directory/'requests.jsonl')
    recovery=source.jsonl(directory/'recovery_requests.jsonl')
    events=source.jsonl(directory/'events.jsonl')
    row=source.read_json(directory/'feature_row.json')
    metadata=source.read_json(directory/'capture_metadata.json')
    truth=source.read_json(directory/'protected_ground_truth.json')
    if (row.get('schema_version')!='faultweave-v2-feature-row.1'
            or row.get('run_id')!=run_id or row.get('label')!=capture.LABEL
            or row.get('split')!=split or row.get('scenario_type')!='known_fault'
            or row.get('interval')!='fault' or row.get('training_eligible') is not True
            or row.get('threshold_tuning_eligible') is not (split=='validation')
            or row.get('window_seconds')!=60 or not isinstance(row.get('features'),dict)
            or set(row['features'])!=set(FEATURE_NAMES) or len(row['features'])!=65
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in row['features'].values())):
        raise ValueError('STOP: auth burst 65-feature label/schema mismatch')
    if (truth.get('run_id')!=run_id or truth.get('fault_id')!=capture.LABEL
            or truth.get('target')!=capture.TARGET or truth.get('intensity')!=capture.INTENSITY
            or truth.get('truth_role')!='audit_only_not_model_feature'
            or metadata.get('healthy_pre_post') is not True
            or metadata.get('mechanism')!='auth-rejection-burst'
            or metadata.get('scheduled_rps')!=capture.RPS
            or metadata.get('scheduled_requests')!=len(req)
            or len(req)!=round(capture.DURATION*capture.RPS)):
        raise ValueError('STOP: auth truth, request volume, or origin mismatch')
    request_ids=[r.get('request_id') for r in req+recovery]
    if (len(request_ids)!=len(set(request_ids))
            or any(not isinstance(rid,str) or not rid.startswith(run_id+'-request-') for rid in request_ids)
            or any(r.get('run_id')!=run_id or not r.get('trace_id') or not r.get('started_at') for r in req+recovery)
            or any(r.get('scenario')!='invalid_login' for r in req)
            or any(r.get('scenario')!='valid' for r in recovery)
            or any(e.get('run_id')!=run_id for e in events)):
        raise ValueError('STOP: auth raw request or correlated event provenance mismatch')
    correlation_gate(req+recovery,events)
    event_watermark=max(parsed_time(e['timestamp']) for e in events)
    if parsed_time(metadata['telemetry_watermark'])!=event_watermark:
        raise ValueError('STOP: raw telemetry watermark mismatch')
    start,end=parsed_time(metadata['phase_started_at']),parsed_time(metadata['phase_ended_at'])
    if not parsed_time(intent['collection_started_at'])<=start<end:
        raise ValueError('STOP: observations start before immutable training intent')
    for key in ('activated_at','expires_at','cleared_at'):
        if parsed_time(metadata['fault_'+key])!=parsed_time(truth[key]):
            raise ValueError('STOP: activation and protected ground truth mismatch')
    window=select_exact_window(phase_started_at=start,phase_ended_at=end,
                               telemetry_watermark=event_watermark,
                               fault_activated_at=truth['activated_at'],
                               fault_expires_at=truth['expires_at'],
                               fault_cleared_at=truth['cleared_at'],
                               fault_required=True,lead_guard_seconds=2,tail_guard_seconds=2)
    if (manifest.get('window_started_at')!=window.started_at.isoformat()
            or manifest.get('window_ended_at')!=window.ended_at.isoformat()
            or row.get('window_started_at')!=window.started_at.isoformat()
            or row.get('window_ended_at')!=window.ended_at.isoformat()):
        raise ValueError('STOP: auth burst 60s window boundary mismatch')
    recovery_proof=recovery_gate(recovery,events,cleared_at=parsed_time(truth['cleared_at']))
    if recovery_proof!=metadata.get('recovery'):
        raise ValueError('STOP: independent auth recovery proof mismatch')
    observed=capture.effect_gate(window,req,events)
    if observed!=metadata.get('impact'):
        raise ValueError('STOP: auth burst impact differs from independently observed 401 evidence')
    vector=build_observable_features(run_id=run_id,window=window,
                                     requests=req,events=events,require_boundary_evidence=True)
    if vector!=row['features']:
        raise ValueError('STOP: independently recomputed 65-feature vector mismatch')
    result={'schema_version':'faultweave-v2-independent-audit.1',
            'run_id':run_id,'label':capture.LABEL,'split':split,
            'verdict':'VALID_V2_TRAINING_RUN','independent':True,
            'request_count':len(req),'correlated_event_count':len(events),
            'recovery_count':len(recovery),'window_seconds':60,'feature_count':65,
            'source_manifest_sha256':base.digest(manifest_path.read_bytes())}
    if write:
        base.write_new(directory/'independent_audit.json',base.serial(result))
        receipt={'run_id':run_id,'label':capture.LABEL,'split':split,
                 'scenario_type':'known_fault','training_admission':True,
                 'audit_verdict':result['verdict'],
                 'source_manifest':{'path':manifest_path.relative_to(base.ROOT).as_posix(),
                                    'sha256':result['source_manifest_sha256']},
                 'independent_audit':{'path':(directory/'independent_audit.json').relative_to(base.ROOT).as_posix(),
                                      'sha256':base.digest(base.serial(result))}}
        base.write_new(directory/'admission_receipt.json',base.serial(receipt))
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--check',action='store_true');g.add_argument('--admit',action='store_true')
    p.add_argument('--slot',type=int,required=True)
    a=p.parse_args(argv)
    print(json.dumps(audit(a.slot,write=a.admit),indent=2))
    print('ADMITTED FRESH AUTH BURST CAPTURE' if a.admit else 'READ ONLY — no admission writes')

if __name__=='__main__':
    try: main()
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        print('V2 INDEPENDENT AUTH AUDIT STOP:',exc,file=sys.stderr);raise SystemExit(2)
