from scripts.verify_phase2b import EXPECTED_SERVICES, validate_run


def test_validate_run_accepts_frozen_topology_and_correlation() -> None:
    run_id = "run-test"
    request_id = "request-test"
    trace_id = "trace-test"
    events = []
    for service in EXPECTED_SERVICES:
        events.append(
            {
                "schema_version": "1.1",
                "timestamp": "2026-09-06T00:00:00Z",
                "service": service,
                "environment": "test",
                "level": "INFO",
                "event_type": "http_request_completed",
                "message": "completed",
                "run_id": run_id,
                "request_id": request_id,
                "trace_id": trace_id,
                "outcome": "success",
                "success": True,
            }
        )
    for source, target in {
        ("gateway", "authentication"),
        ("gateway", "account"),
        ("gateway", "transaction"),
        ("transaction", "account"),
        ("transaction", "payment"),
        ("transaction", "ledger"),
        ("payment", "ledger"),
    }:
        events.append(
            {
                "schema_version": "1.1",
                "timestamp": "2026-09-06T00:00:00Z",
                "service": source,
                "environment": "test",
                "level": "INFO",
                "event_type": "downstream_request_completed",
                "message": "completed",
                "run_id": run_id,
                "request_id": request_id,
                "trace_id": trace_id,
                "outcome": "success",
                "success": True,
                "downstream_service": target,
            }
        )
    for event_type in {
        "transaction_flow_started",
        "authentication_succeeded",
        "account_lookup_completed",
        "account_validation_completed",
        "transaction_created",
        "payment_completed",
        "ledger_entry_created",
        "transaction_completed",
        "transaction_flow_completed",
    }:
        events.append(
            {
                "schema_version": "1.1",
                "timestamp": "2026-09-06T00:00:00Z",
                "service": "gateway",
                "environment": "test",
                "level": "INFO",
                "event_type": event_type,
                "message": "completed",
                "run_id": run_id,
                "request_id": request_id,
                "trace_id": trace_id,
                "outcome": "success",
                "success": True,
            }
        )
    assert validate_run(events, run_id, {request_id: trace_id}) == []


def test_validate_run_rejects_mixed_trace() -> None:
    event = {
        "schema_version": "1.1",
        "timestamp": "2026-09-06T00:00:00Z",
        "service": "gateway",
        "environment": "test",
        "level": "INFO",
        "event_type": "http_request_completed",
        "message": "completed",
        "run_id": "run-test",
        "request_id": "request-test",
        "trace_id": "wrong-trace",
        "outcome": "success",
        "success": True,
    }
    errors = validate_run([event], "run-test", {"request-test": "trace-test"})
    assert any("trace mismatch" in error for error in errors)
