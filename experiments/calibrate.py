from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from experiments.manifest import write_json
from experiments.runner import DEFAULT_OUTPUT_ROOT, run_experiment

DEFAULT_CANDIDATES = (20.0, 30.0, 40.0)


def calibration_passed(manifest, target_rps: float, p95_limit_ms: float) -> bool:
    summary = manifest.summary
    return (
        summary.expected_outcome_rate >= 0.99
        and summary.transport_errors == 0
        and summary.server_errors == 0
        and summary.achieved_rps >= target_rps * 0.90
        and summary.latency.p95_ms <= p95_limit_ms
    )


def calibrate(
    candidates: tuple[float, ...],
    duration_seconds: float,
    p95_limit_ms: float,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict:
    observations: list[dict] = []
    accepted: list[float] = []
    for index, candidate in enumerate(sorted(set(candidates)), start=1):
        try:
            manifest = run_experiment(
                "high_healthy",
                36000 + index,
                duration_seconds=duration_seconds,
                target_rps=candidate,
                output_root=output_root,
                purpose="calibration",
            )
            passed = calibration_passed(manifest, candidate, p95_limit_ms)
            observations.append(
                {
                    "target_rps": candidate,
                    "passed": passed,
                    "run_id": manifest.run_id,
                    "achieved_rps": manifest.summary.achieved_rps,
                    "p95_latency_ms": manifest.summary.latency.p95_ms,
                    "expected_outcome_rate": manifest.summary.expected_outcome_rate,
                    "server_errors": manifest.summary.server_errors,
                    "transport_errors": manifest.summary.transport_errors,
                }
            )
            if passed:
                accepted.append(candidate)
        except (OSError, RuntimeError, ValueError) as exc:
            observations.append(
                {
                    "target_rps": candidate,
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )

    selected = max(accepted) if accepted else None
    result = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "profile": "high_healthy",
        "duration_seconds_per_candidate": duration_seconds,
        "p95_latency_limit_ms": p95_limit_ms,
        "minimum_throughput_ratio": 0.90,
        "selected_high_healthy_rps": selected,
        "observations": observations,
    }
    write_json(output_root / "calibration" / "healthy_envelope.json", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate normal high-but-healthy traffic")
    parser.add_argument("--candidates", default="20,30,40")
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--p95-limit-ms", type=float, default=1000.0)
    args = parser.parse_args()
    try:
        args.parsed_candidates = tuple(float(value) for value in args.candidates.split(","))
    except ValueError as exc:
        parser.error(f"invalid --candidates: {exc}")
    if not args.parsed_candidates or any(value <= 0 for value in args.parsed_candidates):
        parser.error("all candidates must be positive")
    if args.duration <= 0 or args.p95_limit_ms <= 0:
        parser.error("duration and latency limit must be positive")
    return args


def main() -> int:
    args = parse_args()
    result = calibrate(
        args.parsed_candidates,
        args.duration,
        args.p95_limit_ms,
    )
    for observation in result["observations"]:
        status = "PASS" if observation["passed"] else "REJECT"
        details = ""
        if "achieved_rps" in observation:
            details = (
                f" achieved={observation['achieved_rps']:.2f}"
                f" p95={observation['p95_latency_ms']:.2f}ms"
            )
        elif "error" in observation:
            details = f" error={observation['error']}"
        print(f"{status}: target={observation['target_rps']:.2f}{details}")
    selected = result["selected_high_healthy_rps"]
    if selected is None:
        print("FAIL: No candidate met the high-but-healthy acceptance limits")
        return 1
    print(f"PASS: Calibrated high-but-healthy envelope at {selected:.2f} req/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
