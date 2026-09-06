import json

from scripts.collect_logs import extract_event


def test_extract_event_removes_compose_prefix() -> None:
    event = {
        "schema_version": "1.1",
        "timestamp": "2026-09-06T10:00:00Z",
        "service": "gateway",
        "environment": "test",
        "level": "INFO",
        "event_type": "http_request_completed",
        "message": "HTTP request completed",
        "run_id": "run-001",
        "request_id": "request-001",
        "trace_id": "trace-001",
        "outcome": "success",
        "success": True,
    }
    line = f"faultweave-gateway-1  | {json.dumps(event)}"
    assert extract_event(line) == event


def test_extract_event_ignores_plain_runtime_output_and_wrong_schema() -> None:
    assert extract_event("gateway | Application startup complete") is None
    assert extract_event('gateway | {"schema_version":"0.9"}') is None
