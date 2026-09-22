from datasets.final_features import FEATURE_NAMES, PROTECTED_FIELDS, feature_schema


def test_feature_schema_declares_observable_and_protected_fields() -> None:
    schema = feature_schema()
    assert tuple(schema["feature_names"]) == FEATURE_NAMES
    assert tuple(schema["protected_fields"]) == PROTECTED_FIELDS
    assert "fault_id" in schema["protected_fields"]
    assert "fault_id" not in schema["feature_names"]


def test_feature_schema_compares_all_frozen_window_sizes() -> None:
    assert feature_schema()["window_seconds"] == [10, 30, 60]
