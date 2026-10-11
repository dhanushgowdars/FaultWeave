from app.demo_orchestration import DEMO_SCENARIOS, DemoSessionCreate, DemoSessionStore, new_session


def test_phase18_exposes_six_runs_and_seven_investigation_states(tmp_path) -> None:
    expected_scenarios = {
        "normal-transfer",
        "payment-service-unavailable",
        "ledger-service-unavailable",
        "account-response-timeout",
        "ledger-response-timeout",
        "account-error-response",
    }
    assert set(DEMO_SCENARIOS) == expected_scenarios

    investigation_states = [(scenario_id, "after_execution") for scenario_id in expected_scenarios]
    investigation_states.append(("ledger-service-unavailable", "after_recovery"))
    assert len(investigation_states) == 7

    session = new_session(
        DemoSessionCreate(
            scenario_id="ledger-service-unavailable",
            destination_account_number="FW-DEMO-RECIPIENT",
            sender_name="Dhanush R S",
            recipient_name="Ananya Rao",
            payment_channel="UPI",
            purpose="Judge demonstration",
        ),
        "engineer-id",
    )
    store = DemoSessionStore(tmp_path)
    store.save(session)

    restored = store.list(limit=10)
    assert len(restored) == 1
    assert restored[0]["scenario"]["scenario_id"] == "ledger-service-unavailable"
    assert restored[0]["presentation"]["recipient_name"] == "Ananya Rao"
    assert restored[0]["presentation"]["payment_channel"] == "UPI"
