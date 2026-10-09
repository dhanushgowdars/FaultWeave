from faultweave_common.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    encoded = hash_password("correct-password")
    assert verify_password("correct-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_access_token_round_trip() -> None:
    token = create_access_token("user-123")
    claims = decode_access_token(token)
    assert claims["sub"] == "user-123"
    assert claims["role"] == "customer"


def test_engineer_role_round_trip() -> None:
    claims = decode_access_token(create_access_token("engineer-123", role="engineer"))
    assert claims["sub"] == "engineer-123"
    assert claims["role"] == "engineer"
