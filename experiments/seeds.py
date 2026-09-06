from __future__ import annotations

OFFICIAL_NORMAL_SEEDS: dict[str, tuple[int, ...]] = {
    "low": (31001, 31002, 31003, 31004, 31005),
    "medium": (32001, 32002, 32003, 32004, 32005),
    "high_healthy": (33001, 33002, 33003, 33004, 33005),
    "short_burst": (34001, 34002, 34003, 34004, 34005),
    "normal_errors": (35001, 35002, 35003, 35004, 35005),
}


def official_seed_pairs() -> list[tuple[str, int]]:
    return [
        (profile, seed)
        for profile, seeds in OFFICIAL_NORMAL_SEEDS.items()
        for seed in seeds
    ]
