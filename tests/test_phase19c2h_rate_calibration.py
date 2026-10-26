"""No network, Docker, logging, or real data side effects."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.phase19c2h_rate_calibration import (
    checked_rps, safe_url, proven_client_rows, visible_events,
    validate_correlations, json_bytes, jsonl_bytes, main,
)


def example(run="v2cal-abcdefghijklmnop"):
    request = dict(request_id=run+"-request-00001", trace_id="trace-1",
        started_at="2026-10-11T09:00:00Z", latency_ms=21.0, status_code=200,
        expected_outcome=True, transport_error=None, scenario="valid")
    event = dict(run_id=run, request_id=request["request_id"],trace_id="trace-1",
        timestamp="2026-10-11T09:00:00Z", event_type="http_request_completed",
        service="gateway",path="/api/v1/transactions",status_code=200,level="INFO",
        success=True,latency_ms=21.0, password="DO_NOT_SAVE")
    return run, request, event


def test_dry_run_has_no_side_effects(monkeypatch,capsys):
    def forbidden(*a,**kw):
        raise AssertionError("unexpected side effect")
    monkeypatch.setattr("subprocess.run", forbidden)
    assert main(["--dry-run"]) == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_safe_localhost_only():
    assert safe_url("http://localhost:18110") == "http://127.0.0.1:18110"
    for url in ["https://127.0.0.1:18110", "http://127.0.0.1:18111",
                "http://api.example.com:18110", "http://127.0.0.1:18110/a",
                "http://username:pw@localhost:18110", "http://localhost:18110/?foo=bar"]:
        with pytest.raises(ValueError, match="Only the local"):
            safe_url(url)


def test_rate_bounds_and_min_observations():
    assert checked_rps(2.5) == 2.5
    for rate in [0.5,1.0,1.5,2.0,2.501,3.0,float("nan"),float("inf")]:
        with pytest.raises(ValueError):
            checked_rps(rate)


def test_proven_new_requests_carry_explicit_run_id():
    run,request,_=example()
    rows=proven_client_rows([request],run)
    assert rows[0]["run_id"] == run and rows[0]["request_id"] == request["request_id"]
    assert "password" not in rows[0]


def test_request_id_not_from_run_rejected():
    run,request,_=example()
    with pytest.raises(ValueError,match="does not match"):
        proven_client_rows([{**request,"request_id":"historic-v1-0001"}],run)


def test_duplicate_client_rejected():
    run,request,_=example()
    with pytest.raises(ValueError,match="does not match"):
        proven_client_rows([request,request],run)


def test_invalid_scenario_rejected():
    run,request,_=example()
    with pytest.raises(ValueError,match="valid"):
        proven_client_rows([{**request,"scenario":"invalid_login"}],run)


def test_allowlist_omits_secrets():
    run,_,event=example()
    rows=visible_events([event],run)
    assert "password" not in rows[0]
    assert rows[0]["service"]=="gateway"


def test_mixed_run_events_rejected():
    run,_,event=example()
    with pytest.raises(ValueError,match="Mixed-run"):
        visible_events([{**event,"run_id":"other"}],run)


def test_healthy_correlation_success():
    run,request,event=example()
    validate_correlations(proven_client_rows([request],run),visible_events([event],run))


def test_correlation_mismatch_rejected():
    run,request,event=example()
    with pytest.raises(ValueError,match="correlated"):
        validate_correlations(proven_client_rows([request],run),visible_events([{**event,"trace_id":"wrong"}],run))


def test_healthy_non_200_rejected():
    run,request,event=example()
    with pytest.raises(ValueError,match="non-success"):
        validate_correlations(proven_client_rows([{**request,"status_code":500}],run),visible_events([event],run))


def test_nonfinite_json_fail_closed():
    with pytest.raises(ValueError):
        json_bytes({"x":float("nan")})
    with pytest.raises(ValueError):
        jsonl_bytes([{"x":float("inf")}])


def test_original_rate_cap_remains_unchanged():
    from scripts.phase19c2c_healthy_pilot import checked_rps as old_checked
    assert old_checked(2.0) == 2.0
    with pytest.raises(ValueError):
        old_checked(2.5)


def test_calibration_invalid_rate_rejected_even_for_dry_run():
    with pytest.raises(ValueError, match="exactly 2.5"):
        main(["--dry-run", "--rps", "10"])


def test_calibration_default_dry_run(capsys):
    assert main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "2.5" in output and "calibration" in output
