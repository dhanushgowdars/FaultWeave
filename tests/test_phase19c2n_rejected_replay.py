"""Offline rejected resource evidence replay is read-only and preserves verdict."""
import hashlib
import json
from datetime import datetime, UTC, timedelta
import pytest
from scripts.phase19c2n_rejected_replay import analyze


def save(root, corruption=False):
    rid='v2resource-0123456789abcdef'
    folder=root/'data/datasets/v2/rejected-resource'/rid
    folder.mkdir(parents=True)
    start=datetime(2026,10,11,tzinfo=UTC)
    end=start+timedelta(seconds=60)
    st=(start+timedelta(seconds=2)).isoformat()
    rows={
        'requests.jsonl':[dict(request_id='req1',trace_id='tr1',started_at=st,status_code=503,transport_error=None)],
        'recovery_requests.jsonl':[dict(status_code=200,transport_error=None)],
        'events.jsonl':[dict(request_id='req1',trace_id='tr1',timestamp=st,service='gateway',event_type='http_request_completed',status_code=503,success=False)],
        'database_samples.jsonl':[dict(timestamp=st,total_connections=42,advisory_waiters=0)]}
    inv={}
    for name, items in rows.items():
        raw=(''.join(json.dumps(x,sort_keys=True)+'\n' for x in items)).encode()
        (folder/name).write_bytes(raw)
        inv[name]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    manifest=dict(run_id=rid,verdict='REJECTED_RESOURCE_EFFECT_GATE',training_eligible=False,
                  window_started_at=start.isoformat(),window_ended_at=end.isoformat(),artifacts=inv)
    (folder/'manifest.json').write_text(json.dumps(manifest))
    if corruption:
        (folder/'events.jsonl').write_text('tampered')
    return rid,folder


def test_replay_verified_retains_rejected(tmp_path,capsys):
    rid,folder=save(tmp_path)
    before={p.name:p.read_bytes() for p in folder.iterdir()}
    analyze(tmp_path,rid)
    text=capsys.readouterr().out
    assert 'same-run correlated server failures: 1' in text
    assert 'ORIGINAL VERDICT REMAINS REJECTED' in text
    assert {p.name:p.read_bytes() for p in folder.iterdir()}==before


def test_replay_rejects_integrity_corruption(tmp_path):
    rid,_=save(tmp_path,True)
    with pytest.raises(ValueError,match='checksum'):
        analyze(tmp_path,rid)


def test_replay_refuses_nonresource_identity(tmp_path):
    with pytest.raises(ValueError,match='identity'):
        analyze(tmp_path,'v2-normal-low-01')
