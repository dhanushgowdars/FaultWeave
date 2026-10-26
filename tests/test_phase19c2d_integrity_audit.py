"""Synthetic end-to-end pilot evidence; no real Docker or bank traffic."""
from datetime import UTC, datetime, timedelta
import pytest

from scripts.phase19c2d_fault_pilot import (
    fault_evidence_gate, recovery_gate, persist,
)
from scripts.phase19c2d_integrity_audit import audit
from datasets.phase19c2b_exact_windows import select_exact_window, build_observable_features

RUN="v2fault-0123456789abcdef"
START=datetime(2026,10,11,9,0,tzinfo=UTC)


def build_fixture(tmp_path,monkeypatch):
    import scripts.phase19c2d_fault_pilot as pilot
    monkeypatch.setattr(pilot,"OUT_DIR",tmp_path/"data/datasets/v2/pilot-fault")
    requests=[]
    events=[]
    for i in range(150):
        ts=START+timedelta(seconds=i*.5)
        status=500 if i%2 else 200
        ident=f"{RUN}-request-{i+1:05d}"
        row={"run_id":RUN,"request_id":ident,"trace_id":f"t-{i+1}","started_at":ts.isoformat(),
             "status_code":status,"expected_outcome":status==200,"transport_error":None,
             "scenario":"valid","latency_ms":50.0}
        requests.append(row)
        events.append({"run_id":RUN,"request_id":ident,"trace_id":row["trace_id"],
                       "timestamp":(ts+timedelta(milliseconds=20)).isoformat(),
                       "service":"payment","event_type":"http_request_completed",
                       "path":"/internal/v1/payments","status_code":status,
                       "level":"ERROR" if status==500 else "INFO","success":status==200,"latency_ms":50.0})
    cleared=START+timedelta(seconds=77)
    recovery=[]
    for i in range(8):
        ts=cleared+timedelta(seconds=1+i*.5)
        ident=f"{RUN}-request-{151+i:05d}"
        row={"run_id":RUN,"request_id":ident,"trace_id":f"r-{i}","started_at":ts.isoformat(),
             "status_code":200,"expected_outcome":True,"transport_error":None,
             "scenario":"valid","latency_ms":50.0}
        recovery.append(row)
        events.append({"run_id":RUN,"request_id":ident,"trace_id":row["trace_id"],
                       "timestamp":(ts+timedelta(milliseconds=20)).isoformat(),
                       "service":"payment","event_type":"http_request_completed",
                       "path":"/internal/v1/payments","status_code":200,
                       "level":"INFO","success":True,"latency_ms":50.0})
    truth={"schema_version":"19c2d.pilot.1","run_id":RUN,"fault_id":"DOWNSTREAM_ERROR_BURST",
           "target":"payment","intensity":"medium","activated_at":(START-timedelta(seconds=1)).isoformat(),
           "expires_at":(START+timedelta(seconds=105)).isoformat(),"cleared_at":cleared.isoformat(),
           "truth_role":"post-observation validity only"}
    meta={"phase_started_at":START.isoformat(),"phase_ended_at":(START+timedelta(seconds=75)).isoformat(),
          "telemetry_watermark":max(e["timestamp"] for e in events),
          "scheduled_rps":2.0,"scheduled_requests":150,"observed_requests":150,
          "observed_events":len(events),"passed_pre_post_stack_health":True,
          "feature_schema_version":"2.0-candidate-unchanged-65"}
    window=select_exact_window(phase_started_at=meta["phase_started_at"], phase_ended_at=meta["phase_ended_at"],
             telemetry_watermark=meta["telemetry_watermark"],
             fault_activated_at=truth["activated_at"],fault_expires_at=truth["expires_at"],
             fault_cleared_at=truth["cleared_at"],fault_required=True,
             lead_guard_seconds=2,tail_guard_seconds=2)
    features=build_observable_features(run_id=RUN,window=window,requests=requests,
                                      events=events,require_boundary_evidence=True)
    meta["impact"]=fault_evidence_gate(window,requests,events)
    meta["recovery"]=recovery_gate(recovery,events,cleared_at=cleared)
    out=persist(RUN,requests,recovery,events,window,features,truth,meta)
    return out


def test_full_independent_audit(tmp_path, monkeypatch,capsys):
    out=build_fixture(tmp_path,monkeypatch)
    assert audit(tmp_path,out)==0
    assert "65/65 identical" in capsys.readouterr().out


def test_reject_tampered_artifact(tmp_path,monkeypatch):
    out=build_fixture(tmp_path,monkeypatch)
    with (out/"requests.jsonl").open("a",encoding="utf8") as fd:
        fd.write("{}\n")
    with pytest.raises(ValueError, match="Checksum/size mismatch"):
        audit(tmp_path,out)


def test_reject_wrong_output_root(tmp_path,monkeypatch):
    out=build_fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError, match="directly"):
        audit(tmp_path,tmp_path)
