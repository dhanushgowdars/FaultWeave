from scripts.verify_phase3 import contains_forbidden_key


def test_forbidden_event_keys_are_detected_recursively() -> None:
    assert contains_forbidden_key({"attributes": {"fault_active": True}})
    assert contains_forbidden_key({"items": [{"fault_label": "hidden"}]})
    assert not contains_forbidden_key({"attributes": {"profile": "normal"}})
