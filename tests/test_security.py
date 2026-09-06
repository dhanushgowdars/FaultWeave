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
    assert decode_access_token(token)["sub"] == "user-123"
