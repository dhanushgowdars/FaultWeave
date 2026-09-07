import json

import pytest

from experiments.faults import traffic_probe


def test_high_load_rates_are_strictly_above_calibrated_envelope(tmp_path, monkeypatch) -> None:
    calibration = tmp_path / "healthy_envelope.json"
    calibration.write_text(
        json.dumps({"selected_high_healthy_rps": 12.0}), encoding="utf-8"
    )
    monkeypatch.setattr(traffic_probe, "CALIBRATION_PATH", calibration)
    assert traffic_probe.fault_rate("HIGH_LOAD", "mild") == 18.0
    assert traffic_probe.fault_rate("HIGH_LOAD", "medium") == 24.0
    assert traffic_probe.fault_rate("HIGH_LOAD", "high") == 30.0


def test_authentication_burst_rates_exceed_normal_traffic() -> None:
    assert traffic_probe.fault_rate("AUTHENTICATION_FAILURE_BURST", "mild") == 20.0
    assert traffic_probe.fault_rate("AUTHENTICATION_FAILURE_BURST", "high") == 60.0
    payload = traffic_probe.request_payload("AUTHENTICATION_FAILURE_BURST", 1)
    assert payload["password"] != "faultweave-demo"


def test_pool_pressure_uses_low_probe_traffic() -> None:
    assert traffic_probe.fault_rate("CONNECTION_POOL_EXHAUSTION", "high") == 3.0


def test_pool_pressure_intensities_are_bounded() -> None:
    assert traffic_probe._POOL_CONNECTIONS == {"mild": 8, "medium": 12, "high": 15}


def test_missing_calibration_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(traffic_probe, "CALIBRATION_PATH", tmp_path / "missing.json")
    with pytest.raises(RuntimeError, match="calibration"):
        traffic_probe.calibrated_healthy_rps()
