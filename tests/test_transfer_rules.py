from types import SimpleNamespace
from uuid import uuid4

from faultweave_common.schemas import TransferCreate


def test_transfer_rejections_before_balance_mutation() -> None:
    # Use the real account service rules without needing a database connection.
    from services.account.app.main import transfer_rejection

    source = SimpleNamespace(id=str(uuid4()), is_active=True, currency="INR", balance_minor=100)
    destination = SimpleNamespace(id=str(uuid4()), is_active=True, currency="INR")
    payload = TransferCreate(
        source_account_number="FW-DEMO-001",
        destination_account_number="FW-RECEIVER-001",
        amount_minor=101,
        currency="INR",
        idempotency_key="unique-key-001",
    )
    assert transfer_rejection(source, destination, payload) == "insufficient_balance"
    assert source.balance_minor == 100
    payload.amount_minor = 100
    assert transfer_rejection(source, destination, payload) is None
    destination.id = source.id
    assert transfer_rejection(source, destination, payload) == "source_and_destination_must_differ"
    destination.id = str(uuid4())
    destination.currency = "USD"
    assert transfer_rejection(source, destination, payload) == "currency_mismatch"
