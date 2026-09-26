from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from experiments.runner import build_plan, execute_plan  # noqa: E402
from experiments.traffic_profiles import PROFILES, get_profile  # noqa: E402
from scripts.live_request_telemetry import LiveRequestTelemetryPublisher  # noqa: E402


async def run_live_traffic(args: argparse.Namespace) -> int:
    profile = get_profile(args.profile)
    base_rps = args.rps or profile.target_rps
    run_id = f"live-{args.profile}"
    cycle = 0

    async with LiveRequestTelemetryPublisher(
        args.gateway_url,
        batch_size=args.telemetry_batch_size,
        flush_interval=args.telemetry_flush_interval,
    ) as publisher:
        while True:
            cycle_seed = args.seed + cycle
            namespace = f"live-{cycle:06d}"
            plan = build_plan(
                profile,
                seed=cycle_seed,
                duration_seconds=args.duration,
                target_rps=base_rps,
            )
            started = datetime.now(UTC)
            results = await execute_plan(
                plan,
                args.gateway_url,
                run_id,
                cycle_seed,
                args.max_concurrency,
                namespace,
                publisher.observe,
            )
            finished = datetime.now(UTC)
            print(
                "live-traffic "
                f"cycle={cycle} requests={len(results)} "
                f"duration={(finished - started).total_seconds():.2f}s "
                f"telemetry_accepted={publisher.accepted_total} "
                f"telemetry_duplicates={publisher.duplicate_total}"
            )
            cycle += 1
            if args.once:
                break
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate continuous FaultWeave traffic and publish client-observed "
            "request telemetry for frozen live inference"
        )
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="low")
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:18110",
        help="FaultWeave gateway base URL",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Traffic duration per cycle in seconds",
    )
    parser.add_argument("--rps", type=float)
    parser.add_argument("--seed", type=int, default=20260926)
    parser.add_argument("--max-concurrency", type=int, default=100)
    parser.add_argument("--telemetry-batch-size", type=int, default=50)
    parser.add_argument("--telemetry-flush-interval", type=float, default=0.25)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one traffic cycle and exit",
    )
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be positive")
    if args.rps is not None and args.rps <= 0:
        parser.error("--rps must be positive")
    if args.max_concurrency <= 0:
        parser.error("--max-concurrency must be positive")
    if args.telemetry_batch_size <= 0 or args.telemetry_flush_interval <= 0:
        parser.error("telemetry batch size and flush interval must be positive")
    return args


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run_live_traffic(args))
    except KeyboardInterrupt:
        print("\nFaultWeave live traffic stopped.")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: live traffic stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
