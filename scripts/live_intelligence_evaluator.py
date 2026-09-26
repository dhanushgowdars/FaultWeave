from __future__ import annotations

import argparse
import asyncio
import sys

import httpx


async def evaluate_forever(args: argparse.Namespace) -> int:
    endpoint = f"{args.gateway_url.rstrip('/')}/api/v1/intelligence/live/evaluate"
    previous_status: str | None = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                response = await client.post(endpoint)
                if response.status_code == 409:
                    status = "WARMING_UP"
                else:
                    response.raise_for_status()
                    payload = response.json()
                    status = str(payload.get("status") or "UNKNOWN")
                    if status != previous_status or args.verbose:
                        incident = payload.get("incident")
                        incident_id = (
                            incident.get("incident_id")
                            if isinstance(incident, dict)
                            else None
                        )
                        print(
                            f"live-evaluator status={status}"
                            + (f" incident={incident_id}" if incident_id else "")
                        )
                previous_status = status
            except httpx.HTTPError as exc:
                print(f"live-evaluator request failed: {exc}", file=sys.stderr)
                if args.once:
                    return 1

            if args.once:
                return 0
            await asyncio.sleep(args.interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continuously advance FaultWeave live incident evaluation"
    )
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:18110",
        help="FaultWeave gateway base URL",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Evaluate once and exit",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every evaluation rather than status changes only",
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    return args


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(evaluate_forever(args))
    except KeyboardInterrupt:
        print("\nFaultWeave live evaluator stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
