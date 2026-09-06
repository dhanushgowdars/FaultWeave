from scripts.verify_phase2 import (
    EXPECTED_EVENT_TYPES,
    EXPECTED_SERVICES,
    validate_correlated_events,
)


def test_validate_correlated_events_accepts_complete_service_chain() -> None:
    request_id = "phase-2-test"
    events = [
        {
            "request_id": request_id,
            "service": service,
            "event_type": "http_request_completed",
            "latency_ms": 1.2,
            "status_code": 200,
        }
        for service in EXPECTED_SERVICES
    ]
    events.extend(
        {
            "request_id": request_id,
            "service": "gateway",
            "event_type": event_type,
        }
        for event_type in EXPECTED_EVENT_TYPES
    )
    assert validate_correlated_events(events, request_id) == []


def test_validate_correlated_events_reports_missing_service() -> None:
    events = [
        {
            "request_id": "incomplete",
            "service": "gateway",
            "event_type": "http_request_completed",
            "latency_ms": 1.0,
            "status_code": 200,
        }
    ]
    errors = validate_correlated_events(events, "incomplete")
    assert any("missing services" in error for error in errors)
    assert any("missing event types" in error for error in errors)
