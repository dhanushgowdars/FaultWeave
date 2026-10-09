from experiments.faults.catalog import validate_known_fault
from experiments.transfer_fault_cases import TRANSFER_FAULT_CASES, get_transfer_fault_case


def test_transfer_fault_cases_are_explicit_and_catalog_valid() -> None:
    assert set(TRANSFER_FAULT_CASES) == {
        "payment-service-unavailable",
        "ledger-service-unavailable",
        "account-response-timeout",
        "ledger-response-timeout",
        "account-error-response",
    }
    assert {
        item.expected_step for item in TRANSFER_FAULT_CASES.values()
    } == {"payment_execution", "account_commit", "ledger_posting"}
    for item in TRANSFER_FAULT_CASES.values():
        validate_known_fault(item.fault_id, item.target, item.intensity)


def test_committed_funds_are_declared_only_for_post_account_failures() -> None:
    committed = {
        item.case_id
        for item in TRANSFER_FAULT_CASES.values()
        if item.expected_funds_status == "COMMITTED"
    }
    assert committed == {"ledger-service-unavailable", "ledger-response-timeout"}
    assert get_transfer_fault_case("account-response-timeout").expected_funds_status == "UNKNOWN"
