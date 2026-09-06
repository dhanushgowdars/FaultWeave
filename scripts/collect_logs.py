from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {
    "schema_version",
    "timestamp",
    "service",
    "environment",
    "level",
    "event_type",
    "message",
    "request_id",
    "outcome",
}


def extract_event(line: str) -> dict[str, Any] | None:
    payload = line.split("|", 1)[-1].strip()
    if not payload.startswith("{"):
        return None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if event.get("schema_version") != "1.0" or not REQUIRED_FIELDS.issubset(event):
        return None
    return event


def default_output_path() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return PROJECT_DIRECTORY / "data" / "raw" / f"faultweave-logs-{timestamp}.jsonl"


def collect_logs(since: str, output: Path) -> int:
    command = ["docker", "compose", "logs", "--no-color", "--since", since]
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_DIRECTORY,
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        print("FAIL: Docker was not found.", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(f"FAIL: Docker log export failed: {result.stderr.strip()}", file=sys.stderr)
        return 1

    events = [event for line in result.stdout.splitlines() if (event := extract_event(line))]
    if not events:
        print(
            "FAIL: No FaultWeave JSON events were found in the selected interval.",
            file=sys.stderr,
        )
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")

    print(f"PASS: Exported {len(events)} validated JSON events")
    print(f"Output: {output}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export structured FaultWeave Docker logs")
    parser.add_argument("--since", default="10m", help="Docker time range, such as 5m or 1h")
    parser.add_argument("--output", type=Path, default=default_output_path())
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(collect_logs(arguments.since, arguments.output))
