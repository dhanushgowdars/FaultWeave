"""Deterministic quality checks for grounded incident-assistant responses."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

EXPECTED_PROVIDER = "groq"
EXPECTED_MODEL = "openai/gpt-oss-120b"
STATEMENT_FIELDS = (
    "first_failure",
    "funds_state",
    "model_assessment",
    "probable_cause",
    "recovery_assessment",
)
UNSAFE_ACTION_CLAIM = re.compile(
    r"\b(?:i|we)\s+(?:approved|changed|executed|injected|modified|restarted|retried|resumed|"
    r"updated|wrote)\b",
    re.IGNORECASE,
)
RAW_EVENT_ID_IN_PROSE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _words(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _mentions(text: str, value: str) -> bool:
    expected = _words(value)
    return bool(expected) and expected.issubset(_words(text))


def _statement_items(analysis: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result = [
        (name, analysis.get(name) or {})
        for name in STATEMENT_FIELDS
    ]
    result.extend(
        (f"observed_facts[{index}]", item)
        for index, item in enumerate(analysis.get("observed_facts") or [])
        if isinstance(item, dict)
    )
    return result


def _funds_text_matches(text: str, status: str) -> bool:
    words = _words(text)
    return {
        "COMMITTED": "committed" in words,
        "NOT_COMMITTED": {"not", "committed"}.issubset(words),
        "NOT_ATTEMPTED": {"not", "attempted"}.issubset(words),
        "UNKNOWN": "unknown" in words or {"does", "not", "establish"}.issubset(words),
    }.get(status, _mentions(text, status))


def _operational_origin(
    evidence: dict[str, Any], first_failed: dict[str, Any] | None
) -> dict[str, Any] | None:
    history = evidence.get("case_history") or {}
    failures = [item for item in history.get("failures") or [] if isinstance(item, dict)]
    expected_service = {
        "account_commit": "account",
        "ledger_posting": "ledger",
        "payment_execution": "payment",
    }
    specific = next(
        (
            item
            for item in failures
            if str(item.get("service") or "")
            == expected_service.get(str(item.get("step") or ""))
        ),
        None,
    )
    if specific is not None:
        return specific
    if failures and failures[0].get("step"):
        return failures[0]
    return first_failed


def _narrative_text(analysis: dict[str, Any]) -> str:
    values = [
        analysis.get("headline"),
        analysis.get("executive_summary"),
        *(item.get("text") for _, item in _statement_items(analysis)),
        *(analysis.get("recommended_checks") or []),
        *(analysis.get("limitations") or []),
    ]
    return " ".join(str(value or "") for value in values)


def evaluate_assistant_quality(
    response: dict[str, Any],
    evidence: dict[str, Any],
    *,
    lifecycle: Literal["normal", "awaiting_review", "recovered"],
    expected_failure_step: str | None = None,
    forbidden_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Score one AI response without exposing protected fault labels to the provider."""
    analysis = response.get("analysis") or {}
    provenance = response.get("provenance") or {}
    safety = response.get("safety") or {}
    timeline = sorted(
        evidence.get("timeline") or [],
        key=lambda item: (
            item.get("timestamp", ""),
            item.get("service", ""),
            item.get("attempt", 0),
            item.get("sequence", 0),
        ),
    )
    event_index = {
        str(item.get("event_id")): item for item in timeline if item.get("event_id")
    }
    failed_events = [
        item for item in timeline if str(item.get("status") or "").upper() == "FAILED"
    ]
    first_failed = failed_events[0] if failed_events else None
    operational_origin = _operational_origin(evidence, first_failed)
    statements = _statement_items(analysis)
    cited_ids = {
        str(event_id)
        for _, statement in statements
        for event_id in statement.get("evidence_event_ids") or []
    }
    observed = analysis.get("observed_facts") or []
    first_statement = analysis.get("first_failure") or {}
    funds_statement = analysis.get("funds_state") or {}
    recovery_statement = analysis.get("recovery_assessment") or {}
    narrative = json.dumps(analysis, sort_keys=True)
    readable_narrative = _narrative_text(analysis)

    checks: dict[str, bool] = {
        "real_ai_generated": response.get("status") == "AI_GENERATED",
        "expected_provider": provenance.get("provider") == EXPECTED_PROVIDER,
        "expected_model": provenance.get("model") == EXPECTED_MODEL,
        "ground_truth_excluded": provenance.get("ground_truth_used") is False,
        "read_only": safety.get("read_only") is True,
        "no_actions_performed": safety.get("actions_performed") == [],
        "workflow_authoritative": safety.get("workflow_and_journal_remain_authoritative")
        is True,
        "response_event_index_matches_case": set(response.get("evidence_index") or {})
        == set(event_index),
        "all_citations_exist": cited_ids.issubset(event_index),
        "observed_facts_are_cited": bool(observed)
        and all(
            item.get("kind") == "observed_fact" and item.get("evidence_event_ids")
            for item in observed
            if isinstance(item, dict)
        ),
        "observed_fact_depth": len(observed) >= 3,
        "recommendation_depth": len(analysis.get("recommended_checks") or []) >= 3,
        "limitations_present": len(analysis.get("limitations") or []) >= 2,
        "no_claimed_operational_action": UNSAFE_ACTION_CLAIM.search(narrative) is None,
        "event_ids_kept_out_of_prose": RAW_EVENT_ID_IN_PROSE.search(readable_narrative) is None,
        "protected_values_absent": not any(
            value and value in json.dumps(response, sort_keys=True)
            for value in forbidden_values
        ),
    }

    if first_failed is None:
        checks["no_failure_claimed"] = first_statement.get("kind") == "unconfirmed"
        checks["expected_failure_step"] = expected_failure_step is None
    else:
        first_ids = set(first_statement.get("evidence_event_ids") or [])
        checks["first_failure_cites_exact_event"] = first_failed.get("event_id") in first_ids
        checks["first_failure_names_service"] = _mentions(
            str(first_statement.get("text") or ""), str(first_failed.get("service") or "")
        )
        checks["first_failure_names_step"] = _mentions(
            str(first_statement.get("text") or ""), str(first_failed.get("step") or "")
        )
        checks["operational_origin_matches_expected"] = (
            expected_failure_step is None
            or (
                operational_origin is not None
                and operational_origin.get("step") == expected_failure_step
            )
        )
        if expected_failure_step is not None:
            cause_text = " ".join(
                [
                    str(first_statement.get("text") or ""),
                    str((analysis.get("probable_cause") or {}).get("text") or ""),
                    str(analysis.get("executive_summary") or ""),
                ]
            )
            checks["explanation_names_operational_origin"] = _mentions(
                cause_text, expected_failure_step
            )

    funds_status = str(evidence.get("funds_status") or "UNKNOWN")
    checks["funds_state_matches"] = _funds_text_matches(
        str(funds_statement.get("text") or ""), funds_status
    )
    if funds_status == "UNKNOWN":
        outcome_text = " ".join(
            [
                str(analysis.get("executive_summary") or ""),
                str(funds_statement.get("text") or ""),
            ]
        ).lower()
        checks["unknown_funds_not_overclaimed"] = not any(
            phrase in outcome_text
            for phrase in (
                "could not be completed",
                "did not complete",
                "no funds movement",
                "funds did not move",
                "source balance is unchanged",
            )
        )
    workflow_state = str(evidence.get("state") or "UNKNOWN")
    recovery_text = str(recovery_statement.get("text") or "")
    if lifecycle == "awaiting_review":
        checks["lifecycle_matches"] = (
            workflow_state == "REQUIRES_REVIEW" and "review" in _words(recovery_text)
        )
    elif lifecycle == "recovered":
        history = evidence.get("case_history") or {}
        checks["lifecycle_matches"] = (
            workflow_state == "COMPLETED"
            and bool(history.get("recoveries"))
            and bool({"recovered", "recovery", "completed", "verified"} & _words(recovery_text))
        )
    else:
        checks["lifecycle_matches"] = (
            workflow_state == "COMPLETED" and "completed" in _words(recovery_text)
        )

    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "lifecycle": lifecycle,
        "passed": not failed_checks,
        "score": round(sum(checks.values()) / max(1, len(checks)), 6),
        "checks": checks,
        "failed_checks": failed_checks,
        "event_count": len(timeline),
        "cited_event_count": len(cited_ids),
        "first_failed_event": (
            {
                "event_id": first_failed.get("event_id"),
                "service": first_failed.get("service"),
                "step": first_failed.get("step"),
            }
            if first_failed
            else None
        ),
        "operational_origin": (
            {
                "event_id": operational_origin.get("event_id"),
                "service": operational_origin.get("service"),
                "step": operational_origin.get("step"),
            }
            if operational_origin
            else None
        ),
        "headline": analysis.get("headline"),
        "first_failure_text": first_statement.get("text"),
        "funds_state_text": funds_statement.get("text"),
        "recovery_text": recovery_statement.get("text"),
        "recommended_checks": analysis.get("recommended_checks") or [],
    }


def render_quality_report(report: dict[str, Any]) -> str:
    """Render a reviewable, judge-friendly record of the generated explanations."""
    lines = [
        "# Phase 16G — grounded AI explanation quality",
        "",
        f"Overall result: **{'PASS' if report.get('passed') else 'FAIL'}**",
        "",
        "The provider received sanitized observable evidence only. Protected fault labels were",
        "used exclusively by this offline acceptance evaluator and were never sent to Groq.",
        "",
    ]
    for case in report.get("cases") or []:
        evaluation = case["evaluation"]
        response = case["response"]
        analysis = response.get("analysis") or {}
        lines.extend(
            [
                f"## {case['case_id']} — {case['lifecycle']}",
                "",
                f"**{'PASS' if evaluation['passed'] else 'FAIL'} — "
                f"{analysis.get('headline')}**",
                "",
                f"- Customer outcome: {analysis.get('executive_summary')}",
                f"- What broke first: {(analysis.get('first_failure') or {}).get('text')}",
                f"- Why FaultWeave localized it there: "
                f"{(analysis.get('probable_cause') or {}).get('text')}",
                f"- Money safety: {(analysis.get('funds_state') or {}).get('text')}",
                f"- Review / recovery: "
                f"{(analysis.get('recovery_assessment') or {}).get('text')}",
                f"- AI mode: {response.get('status')} via "
                f"{(response.get('provenance') or {}).get('provider')} / "
                f"{(response.get('provenance') or {}).get('model')}",
                f"- Quality gate: {evaluation['score']:.2%}; Failed checks: "
                f"{', '.join(evaluation['failed_checks']) or 'none'}",
                "",
                "### Engineer checks",
                "",
            ]
        )
        lines.extend(
            f"{index}. {item}"
            for index, item in enumerate(analysis.get("recommended_checks") or [], start=1)
        )
        lines.extend(["", "### Evidence-backed observations", ""])
        lines.extend(
            f"- {item.get('text')} (citations: "
            f"{', '.join(item.get('evidence_event_ids') or [])})"
            for item in analysis.get("observed_facts") or []
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
