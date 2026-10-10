from __future__ import annotations

from copy import deepcopy

from experiments.assistant_quality import evaluate_assistant_quality, render_quality_report


def evidence() -> dict:
    return {
        "state": "REQUIRES_REVIEW",
        "funds_status": "UNKNOWN",
        "timeline": [
            {
                "event_id": "event-start",
                "sequence": 1,
                "timestamp": "2026-10-10T00:00:00Z",
                "service": "transaction",
                "step": "transfer_received",
                "status": "SUCCEEDED",
            },
            {
                "event_id": "event-failure",
                "sequence": 2,
                "timestamp": "2026-10-10T00:00:01Z",
                "service": "payment",
                "step": "payment_execution",
                "status": "FAILED",
            },
        ],
        "case_history": {"failures": [{}], "reviews": [], "recoveries": []},
    }


def statement(kind: str, text: str, ids: list[str]) -> dict:
    return {"kind": kind, "text": text, "evidence_event_ids": ids}


def response() -> dict:
    fact = statement("observed_fact", "The payment execution failed.", ["event-failure"])
    return {
        "status": "AI_GENERATED",
        "analysis": {
            "headline": "Transfer requires review after payment execution failure",
            "executive_summary": "The transaction is waiting for engineer review.",
            "first_failure": fact,
            "funds_state": statement(
                "observed_fact", "The funds status remains UNKNOWN.", ["event-failure"]
            ),
            "model_assessment": statement("unconfirmed", "Model unavailable.", []),
            "probable_cause": statement(
                "ai_inference", "Payment was unavailable.", ["event-failure"]
            ),
            "observed_facts": [
                fact,
                statement("observed_fact", "The transfer was received.", ["event-start"]),
                statement(
                    "observed_fact", "Payment did not complete.", ["event-failure"]
                ),
            ],
            "recommended_checks": ["Check payment", "Check health", "Check idempotency"],
            "recovery_assessment": statement(
                "observed_fact", "The case is waiting for engineer review.", ["event-failure"]
            ),
            "limitations": ["Read-only", "Root cause is not confirmed"],
        },
        "evidence_index": {"event-start": {}, "event-failure": {}},
        "provenance": {
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "ground_truth_used": False,
        },
        "safety": {
            "read_only": True,
            "actions_performed": [],
            "workflow_and_journal_remain_authoritative": True,
        },
    }


def test_fault_explanation_passes_objective_quality_contract() -> None:
    result = evaluate_assistant_quality(
        response(),
        evidence(),
        lifecycle="awaiting_review",
        expected_failure_step="payment_execution",
        forbidden_values=("FW-PRIVATE-001",),
    )

    assert result["passed"] is True
    assert result["failed_checks"] == []
    assert result["score"] == 1.0


def test_wrong_first_failure_and_invented_citation_are_rejected() -> None:
    value = deepcopy(response())
    value["analysis"]["first_failure"] = statement(
        "observed_fact", "The ledger posting failed.", ["invented-event"]
    )
    result = evaluate_assistant_quality(
        value,
        evidence(),
        lifecycle="awaiting_review",
        expected_failure_step="payment_execution",
    )

    assert result["passed"] is False
    assert "all_citations_exist" in result["failed_checks"]
    assert "first_failure_cites_exact_event" in result["failed_checks"]
    assert "first_failure_names_service" in result["failed_checks"]


def test_unknown_funds_cannot_be_reported_as_confirmed_non_commit() -> None:
    value = response()
    value["analysis"]["executive_summary"] = (
        "A timeout occurred, so debit and credit could not be completed."
    )

    result = evaluate_assistant_quality(
        value,
        evidence(),
        lifecycle="awaiting_review",
        expected_failure_step="payment_execution",
    )

    assert result["passed"] is False
    assert "unknown_funds_not_overclaimed" in result["failed_checks"]


def test_downstream_origin_is_distinct_from_transaction_propagation() -> None:
    value = evidence()
    value["timeline"] = [
        value["timeline"][0],
        {
            **value["timeline"][1],
            "event_id": "ledger-failure",
            "timestamp": "2026-10-10T00:00:01Z",
            "service": "payment",
            "step": "ledger_posting",
            "sequence": 5,
        },
        {
            **value["timeline"][1],
            "event_id": "transaction-propagation",
            "timestamp": "2026-10-10T00:00:02Z",
            "service": "transaction",
            "step": "payment_execution",
            "sequence": 3,
        },
    ]
    value["case_history"]["failures"] = [
        {
            "event_id": "ledger-failure",
            "service": "ledger",
            "step": "ledger_posting",
        },
        {
            "event_id": "transaction-propagation",
            "service": "payment",
            "step": "ledger_posting",
        },
    ]
    result_response = response()
    result_response["evidence_index"] = {
        "event-start": {},
        "ledger-failure": {},
        "transaction-propagation": {},
    }
    first = result_response["analysis"]["first_failure"]
    first["text"] = "The payment service failed during ledger posting."
    first["evidence_event_ids"] = ["ledger-failure"]
    result_response["analysis"]["probable_cause"]["text"] = (
        "Evidence localizes the problem to ledger posting."
    )
    result_response["analysis"]["probable_cause"]["evidence_event_ids"] = [
        "ledger-failure"
    ]
    for _, item in [
        ("first_failure", result_response["analysis"]["first_failure"]),
        ("funds_state", result_response["analysis"]["funds_state"]),
        ("probable_cause", result_response["analysis"]["probable_cause"]),
        ("recovery_assessment", result_response["analysis"]["recovery_assessment"]),
        *enumerate(result_response["analysis"]["observed_facts"]),
    ]:
        if item["evidence_event_ids"] == ["event-failure"]:
            item["evidence_event_ids"] = ["ledger-failure"]

    result = evaluate_assistant_quality(
        result_response,
        value,
        lifecycle="awaiting_review",
        expected_failure_step="ledger_posting",
    )

    assert result["passed"] is True
    assert result["operational_origin"]["service"] == "ledger"
    assert result["operational_origin"]["step"] == "ledger_posting"


def test_quality_report_contains_actual_explanation_and_failures() -> None:
    evaluation = evaluate_assistant_quality(
        response(), evidence(), lifecycle="awaiting_review"
    )
    rendered = render_quality_report(
        {
            "passed": True,
            "cases": [
                {
                    "case_id": "payment-service-unavailable",
                    "lifecycle": "awaiting_review",
                    "evaluation": evaluation,
                    "response": response(),
                }
            ],
        }
    )

    assert "grounded AI explanation quality" in rendered
    assert "payment-service-unavailable" in rendered
    assert "The funds status remains UNKNOWN" in rendered
    assert "Failed checks: none" in rendered
