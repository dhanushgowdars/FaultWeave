from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from faultweave_common.logging import StructuredJsonFormatter, StructuredLogEvent, redact
from pydantic import ValidationError


def test_structured_formatter_emits_required_schema_and_redacts_secrets() -> None:
    formatter = StructuredJsonFormatter(service="authentication", environment="test")
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Rejected password=plain-text and Bearer abc.def.ghi",
        args=(),
        exc_info=None,
    )
    record.faultweave_event = {
        "event_type": "authentication_failed",
        "request_id": "request-001",
        "outcome": "failure",
        "attributes": {
            "password": "plain-text",
            "authorization": "Bearer abc.def.ghi",
            "attempt": 3,
        },
    }

    event = json.loads(formatter.format(record))

    assert event["schema_version"] == "1.1"
    assert event["service"] == "authentication"
    assert event["level"] == "WARNING"
    assert event["request_id"] == "request-001"
    assert event["success"] is False
    assert "plain-text" not in json.dumps(event)
    assert "abc.def.ghi" not in json.dumps(event)
    assert event["attributes"]["password"] == "[REDACTED]"
    assert event["attributes"]["attempt"] == 3


def test_log_schema_rejects_invalid_status_and_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        StructuredLogEvent(
            timestamp=datetime.now(),
            service="gateway",
            environment="test",
            level="INFO",
            event_type="http_request_completed",
            message="Request completed",
            status_code=99,
        )


def test_redaction_is_recursive_and_preserves_safe_values() -> None:
    value = redact(
        {
            "safe": "visible",
            "nested": {"api_key": "private", "count": 4},
            "items": [{"access_token": "private"}],
        }
    )
    assert value == {
        "safe": "visible",
        "nested": {"api_key": "[REDACTED]", "count": 4},
        "items": [{"access_token": "[REDACTED]"}],
    }


def test_log_schema_accepts_timezone_aware_timestamp() -> None:
    event = StructuredLogEvent(
        timestamp=datetime.now(UTC),
        service="gateway",
        environment="test",
        level="INFO",
        event_type="transaction_flow_started",
        message="Flow started",
    )
    assert event.timestamp.tzinfo is not None
