from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from scripts.collect_logs import extract_event  # noqa: E402

INTELLIGENCE_PATH_PREFIX = "/api/v1/intelligence/"
SERVICE_NAMES = {"gateway", "authentication", "transaction", "payment", "account", "ledger"}


def eligible_event(event: dict[str, Any]) -> bool:
    if str(event.get("service")) not in SERVICE_NAMES:
        return False
    event_type = str(event.get("event_type") or "")
    path = str(event.get("path") or "")
    if event_type == "health_check_completed" or path.startswith("/health/"):
        return False
    if event_type.startswith("intelligence_") or path.startswith(INTELLIGENCE_PATH_PREFIX):
        return False
    return True


async def _post_batch(
    client: httpx.AsyncClient,
    endpoint: str,
    events: list[dict[str, Any]],
) -> tuple[int, int, int]:
    response = await client.post(endpoint, json={"events": events})
    response.raise_for_status()
    payload = response.json()
    return (
        int(payload.get("accepted", 0)),
        int(payload.get("ignored", 0)),
        int(payload.get("duplicates", 0)),
    )


async def bridge(args: argparse.Namespace) -> int:
    endpoint = f"{args.gateway_url.rstrip('/')}/api/v1/intelligence/telemetry"
    process = await asyncio.create_subprocess_exec(
        "docker",
        "compose",
        "logs",
        "--follow",
        "--no-color",
        "--since",
        args.since,
        cwd=PROJECT_DIRECTORY,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if process.stdout is None:
        raise RuntimeError("Docker log stream has no stdout pipe")

    accepted_total = 0
    ignored_total = 0
    duplicate_total = 0
    batch: list[dict[str, Any]] = []
    print(f"FaultWeave live telemetry bridge -> {endpoint}")
    print("Press Ctrl+C to stop.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            timed_out = False
            try:
                line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=args.flush_interval,
                )
            except TimeoutError:
                line = b""
                timed_out = True

            if line:
                event = extract_event(line.decode("utf-8", errors="replace"))
                if event is not None and eligible_event(event):
                    batch.append(event)
            elif not timed_out and process.returncode is not None:
                break

            should_flush = batch and (timed_out or len(batch) >= args.batch_size)
            if should_flush:
                accepted, ignored, duplicates = await _post_batch(client, endpoint, batch)
                accepted_total += accepted
                ignored_total += ignored
                duplicate_total += duplicates
                batch.clear()
                print(
                    "telemetry: "
                    f"accepted={accepted_total} ignored={ignored_total} "
                    f"duplicates={duplicate_total}",
                    end="\r",
                    flush=True,
                )

    if batch:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await _post_batch(client, endpoint, batch)
    error_output = b""
    if process.stderr is not None:
        error_output = await process.stderr.read()
    return_code = await process.wait()
    if return_code != 0:
        message = error_output.decode("utf-8", errors="replace").strip()
        print(f"\nDocker log stream exited with code {return_code}: {message}", file=sys.stderr)
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream live FaultWeave Docker telemetry into the intelligence gateway"
    )
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:18110",
        help="FaultWeave gateway base URL",
    )
    parser.add_argument(
        "--since",
        default="70s",
        help="Initial Docker log lookback used to warm rolling windows",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--flush-interval", type=float, default=0.5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0 or args.flush_interval <= 0:
        print("batch size and flush interval must be positive", file=sys.stderr)
        return 2
    try:
        return asyncio.run(bridge(args))
    except KeyboardInterrupt:
        print("\nLive telemetry bridge stopped.")
        return 0
    except (OSError, httpx.HTTPError, RuntimeError) as exc:
        print(f"FAIL: live telemetry bridge stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
