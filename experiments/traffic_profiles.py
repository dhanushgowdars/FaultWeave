from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioWeights:
    valid: float
    invalid_login: float = 0.0
    invalid_account: float = 0.0
    invalid_amount: float = 0.0

    def __post_init__(self) -> None:
        total = self.valid + self.invalid_login + self.invalid_account + self.invalid_amount
        if abs(total - 1.0) > 1e-9:
            raise ValueError("scenario weights must total 1.0")


@dataclass(frozen=True)
class RateSegment:
    fraction: float
    multiplier: float


@dataclass(frozen=True)
class TrafficProfile:
    name: str
    description: str
    target_rps: float
    duration_seconds: float
    scenario_weights: ScenarioWeights
    rate_segments: tuple[RateSegment, ...] = (RateSegment(1.0, 1.0),)

    def __post_init__(self) -> None:
        if self.target_rps <= 0 or self.duration_seconds <= 0:
            raise ValueError("rate and duration must be positive")
        if abs(sum(segment.fraction for segment in self.rate_segments) - 1.0) > 1e-9:
            raise ValueError("rate segment fractions must total 1.0")


COMMON_NORMAL_MIX = ScenarioWeights(
    valid=0.98,
    invalid_login=0.01,
    invalid_account=0.005,
    invalid_amount=0.005,
)

PROFILES: dict[str, TrafficProfile] = {
    "low": TrafficProfile(
        name="low",
        description="Steady low normal traffic",
        target_rps=3.0,
        duration_seconds=30.0,
        scenario_weights=COMMON_NORMAL_MIX,
    ),
    "medium": TrafficProfile(
        name="medium",
        description="Steady medium normal traffic",
        target_rps=12.0,
        duration_seconds=30.0,
        scenario_weights=COMMON_NORMAL_MIX,
    ),
    "high_healthy": TrafficProfile(
        name="high_healthy",
        description="Calibrated high traffic that remains healthy",
        target_rps=40.0,
        duration_seconds=30.0,
        scenario_weights=ScenarioWeights(valid=0.995, invalid_login=0.005),
    ),
    "short_burst": TrafficProfile(
        name="short_burst",
        description="Healthy short burst surrounded by low traffic",
        target_rps=5.0,
        duration_seconds=30.0,
        scenario_weights=COMMON_NORMAL_MIX,
        rate_segments=(
            RateSegment(0.4, 1.0),
            RateSegment(0.2, 4.0),
            RateSegment(0.4, 1.0),
        ),
    ),
    "normal_errors": TrafficProfile(
        name="normal_errors",
        description="Normal user mistakes without an operational fault",
        target_rps=5.0,
        duration_seconds=30.0,
        scenario_weights=ScenarioWeights(
            valid=0.85,
            invalid_login=0.05,
            invalid_account=0.05,
            invalid_amount=0.05,
        ),
    ),
}


def get_profile(name: str) -> TrafficProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        choices = ", ".join(sorted(PROFILES))
        raise ValueError(f"unknown profile {name!r}; choose from {choices}") from exc


def request_offsets(
    profile: TrafficProfile,
    duration_seconds: float,
    target_rps: float,
) -> list[float]:
    offsets: list[float] = []
    segment_start = 0.0
    for segment in profile.rate_segments:
        segment_duration = duration_seconds * segment.fraction
        segment_rps = target_rps * segment.multiplier
        request_count = max(1, round(segment_duration * segment_rps))
        offsets.extend(segment_start + index / segment_rps for index in range(request_count))
        segment_start += segment_duration
    return [offset for offset in offsets if offset < duration_seconds]
