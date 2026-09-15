import pytest

from experiments.seeds import official_seed_pairs
from experiments.traffic_profiles import (
    PROFILES,
    get_profile,
    request_offsets,
    resolve_rate_segments,
)


def test_all_frozen_normal_profiles_are_present() -> None:
    assert set(PROFILES) == {
        "low",
        "medium",
        "high_healthy",
        "short_burst",
        "normal_errors",
    }


def test_constant_profile_generates_expected_request_count() -> None:
    offsets = request_offsets(get_profile("low"), duration_seconds=10, target_rps=3)
    assert len(offsets) == 30
    assert offsets[0] == 0
    assert offsets[-1] < 10


def test_short_burst_preserves_six_second_peak_at_default_duration() -> None:
    profile = get_profile("short_burst")
    schedule = resolve_rate_segments(profile, duration_seconds=30)
    assert [(item.offset_seconds, item.duration_seconds, item.multiplier) for item in schedule] == [
        (0.0, 12.0, 1.0),
        (12.0, 6.0, 4.0),
        (18.0, 12.0, 1.0),
    ]


def test_short_burst_does_not_stretch_peak_in_final_dataset_run() -> None:
    profile = get_profile("short_burst")
    schedule = resolve_rate_segments(profile, duration_seconds=120)
    assert [(item.offset_seconds, item.duration_seconds, item.multiplier) for item in schedule] == [
        (0.0, 57.0, 1.0),
        (57.0, 6.0, 4.0),
        (63.0, 57.0, 1.0),
    ]
    offsets = request_offsets(profile, duration_seconds=120, target_rps=5)
    burst_offsets = [offset for offset in offsets if 57 <= offset < 63]
    assert len(offsets) == 690
    assert len(burst_offsets) == 120


def test_short_burst_peak_can_be_capped_at_calibrated_healthy_rate() -> None:
    profile = get_profile("short_burst")
    offsets = request_offsets(
        profile,
        duration_seconds=120,
        target_rps=5,
        maximum_rps=12,
    )
    burst_offsets = [offset for offset in offsets if 57 <= offset < 63]
    assert len(offsets) == 642
    assert len(burst_offsets) == 72


def test_short_burst_rejects_duration_shorter_than_fixed_peak() -> None:
    with pytest.raises(ValueError, match="shorter"):
        request_offsets(get_profile("short_burst"), duration_seconds=5, target_rps=5)


def test_official_normal_suite_has_25_unique_profile_seed_pairs() -> None:
    pairs = official_seed_pairs()
    assert len(pairs) == 25
    assert len(set(pairs)) == 25
