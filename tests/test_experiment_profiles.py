from experiments.seeds import official_seed_pairs
from experiments.traffic_profiles import PROFILES, get_profile, request_offsets


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


def test_short_burst_has_more_requests_than_constant_base_rate() -> None:
    profile = get_profile("short_burst")
    offsets = request_offsets(profile, duration_seconds=10, target_rps=5)
    assert len(offsets) == 80


def test_official_normal_suite_has_25_unique_profile_seed_pairs() -> None:
    pairs = official_seed_pairs()
    assert len(pairs) == 25
    assert len(set(pairs)) == 25
