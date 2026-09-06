import pytest
from faultweave_common.schemas import TransactionRequest
from pydantic import ValidationError


def test_transaction_request_normalizes_currency() -> None:
    request = TransactionRequest(
        username="demo",
        password="secret",
        amount_minor=5000,
        currency="inr",
        recipient="merchant-demo",
    )
    assert request.currency == "INR"


@pytest.mark.parametrize("amount", [0, -1, 100_000_001])
def test_transaction_request_rejects_invalid_amount(amount: int) -> None:
    with pytest.raises(ValidationError):
        TransactionRequest(
            username="demo",
            password="secret",
            amount_minor=amount,
            currency="INR",
            recipient="merchant-demo",
        )
