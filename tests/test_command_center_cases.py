"""Read-only case projection contract: engineer scope, evidence, and AI citations."""
from __future__ import annotations

import unittest

from scripts.faultweave_command_center_cases import CaseRouteError, case_detail, list_cases


CASE_ID = "11111111-2222-3333-4444-555555555555"
EVENT_ID = "event-first-failure"


def gateway(port, path, authorization):
    assert port == 18110
    if path == "/api/v1/demo/contract":
        return (200, {}) if authorization == "Bearer engineer" else (403, {})
    if path == "/api/v1/transfers?limit=20&offset=0":
        return 200, {"schema_version": "1.0", "scope": "engineer", "page": {"total": 1, "has_more": False},
                     "items": [{"id": CASE_ID, "state": "REQUIRES_REVIEW", "funds_status": "COMMITTED",
                                "amount_minor": 1000, "currency": "INR", "created_at": "2026-10-15T00:00:00Z",
                                "source_account_number": "SECRET", "idempotency_key": "DO-NOT-LEAK"}]}
    if path == f"/api/v1/transfers/{CASE_ID}/evidence":
        return 200, {"schema_version": "1.0", "transfer_id": CASE_ID, "state": "REQUIRES_REVIEW",
                     "funds_status": "COMMITTED", "requires_review": True,
                     "created_at": "2026-10-15T00:00:00Z",
                     "transfer": {"amount_minor": 1000, "currency": "INR", "idempotency_key": "DO-NOT-LEAK"},
                     "timeline": [{"event_id": EVENT_ID, "timestamp": "2026-10-15T00:00:01Z",
                                   "service": "payment", "step": "ledger_posting", "status": "FAILED",
                                   "detail": "Ledger unavailable", "trace_id": "trace-1"}],
                     "journal": {"entries": [{"direction": "DEBIT", "amount_minor": 1000,
                                               "currency": "INR", "account_id": "SECRET"}]},
                     "case_history": {"failures": [{}], "reviews": [], "recoveries": []},
                     "investigation": {"observed_failure_service": "payment",
                                       "cause_status": "observed_failure_root_cause_unconfirmed",
                                       "evidence_event_ids": [EVENT_ID],
                                       "next_action": "Inspect the failed step"},
                     "evidence_sources": {"workflow": "transactions.transfer_workflows",
                                          "payment": "live_database", "journal": "ledger_persisted_posting"}}
    if path == "/api/v1/demo/sessions?limit=100":
        return 200, {"items": [{"transfer_id": CASE_ID, "analysis_status": "READY", "snapshots": {
            "assistant": {"schema_version": "1.0", "transfer_id": CASE_ID,
                          "status": "DETERMINISTIC_FALLBACK", "provenance": {"ground_truth_used": False,
                                "mode": "deterministic_fallback"},
                          "analysis": {"headline": "Saved evidence explains failure",
                                       "executive_summary": "Ledger posting failed, cited in event.",
                                       **{key: {"kind": "observed_fact", "text": "Observed evidence",
                                                "evidence_event_ids": [EVENT_ID]}
                                          for key in ("first_failure", "funds_state", "model_assessment", "probable_cause")}}}}}]}
    raise AssertionError(path)


class CaseProjectionTests(unittest.TestCase):
    def test_engineer_only_case_list_and_redacted_detail(self):
        with self.assertRaises(CaseRouteError) as denied:
            list_cases(gateway, 18110, "Bearer customer")
        self.assertEqual(denied.exception.status, 403)
        listing = list_cases(gateway, 18110, "Bearer engineer")
        self.assertEqual(listing["items"][0]["id"], CASE_ID)
        self.assertNotIn("SECRET", str(listing))
        detail = case_detail(gateway, 18110, "Bearer engineer", CASE_ID)
        self.assertEqual(detail["timeline"][0]["event_id"], EVENT_ID)
        self.assertEqual(detail["source_model"], "earlier_gateway_frozen_model")
        self.assertEqual(detail["saved_assistant"]["headline"], "Saved evidence explains failure")
        self.assertEqual(detail["localization"]["first_failure"]["event_id"], EVENT_ID)
        self.assertEqual(detail["localization"]["reported_failure_service"], "payment")
        self.assertFalse(detail["localization"]["root_cause_confirmed"])
        self.assertNotIn("SECRET", str(detail))
        self.assertNotIn("DO-NOT-LEAK", str(detail))

    def test_unknown_case_id_rejected_before_gateway(self):
        with self.assertRaises(CaseRouteError) as denied:
            case_detail(gateway, 18110, "Bearer engineer", "../internal/fault")
        self.assertEqual(denied.exception.status, 404)

    def test_saved_ai_with_unrelated_citation_is_omitted(self):
        def ungrounded(port, path, authorization):
            status, value = gateway(port, path, authorization)
            if path == "/api/v1/demo/sessions?limit=100":
                value["items"][0]["snapshots"]["assistant"]["analysis"]["first_failure"]["evidence_event_ids"] = ["invented"]
            return status, value
        detail = case_detail(ungrounded, 18110, "Bearer engineer", CASE_ID)
        self.assertIsNone(detail["saved_assistant"])

    def test_optional_ai_service_failure_does_not_hide_persisted_case(self):
        def no_session_service(port, path, authorization):
            if path == "/api/v1/demo/sessions?limit=100":
                raise TimeoutError("optional saved assistant unavailable")
            return gateway(port, path, authorization)
        detail = case_detail(no_session_service, 18110, "Bearer engineer", CASE_ID)
        self.assertIsNone(detail["saved_assistant"])
        self.assertEqual(detail["timeline"][0]["event_id"], EVENT_ID)

    def test_investigation_cannot_cite_an_unrelated_event(self):
        def ungrounded(port, path, authorization):
            status, value = gateway(port, path, authorization)
            if path.endswith("/evidence"):
                value["investigation"]["evidence_event_ids"] = ["invented"]
            return status, value
        with self.assertRaisesRegex(ValueError, "outside the failed timeline"):
            case_detail(ungrounded, 18110, "Bearer engineer", CASE_ID)

    def test_customer_scoped_list_is_never_relabelled_engineer(self):
        def customer_scope(port, path, authorization):
            status, value = gateway(port, path, authorization)
            if path.startswith("/api/v1/transfers?"):
                value["scope"] = "customer"
            return status, value
        with self.assertRaises(ValueError):
            list_cases(customer_scope, 18110, "Bearer engineer")


if __name__ == "__main__":
    unittest.main()
