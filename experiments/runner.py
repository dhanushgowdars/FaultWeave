from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

from experiments.manifest import (
    ExperimentManifest,
    LatencySummary,
    RunArtifacts,
    RunSummary,
    StatusCount,
    sha256_file,
    write_json,
)
from experiments.traffic_profiles import PROFILES, TrafficProfile, get_profile, request_offsets
from scripts.collect_logs import extract_event

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_DIRECTORY / "data" / "experiments"
TOPOLOGY_PATH = PROJECT_DIRECTORY / "config" / "service_dependencies.json"


@dataclass(frozen=True)
class PlannedRequest:
    sequence: int
    offset_seconds: float
    scenario: str
    amount_minor: int


def select_scenario(rng: random.Random, profile: TrafficProfile) -> str:
    value = rng.random()
    cumulative = 0.0
    for name, weight in (
        ("valid", profile.scenario_weights.valid),
        ("invalid_login", profile.scenario_weights.invalid_login),
        ("invalid_account", profile.scenario_weights.invalid_account),
        ("invalid_amount", profile.scenario_weights.invalid_amount),
    ):
        cumulative += weight
        if value < cumulative:
            return name
    return "valid"


def build_plan(
    profile: TrafficProfile,
    seed: int,
    duration_seconds: float,
    target_rps: float,
) -> list[PlannedRequest]:
    rng = random.Random(seed)
    return [
        PlannedRequest(
            sequence=index,
            offset_seconds=offset,
            scenario=select_scenario(rng, profile),
            amount_minor=rng.randint(100, 100_000),
        )
        for index, offset in enumerate(
            request_offsets(profile, duration_seconds, target_rps),
            start=1,
        )
    ]


def make_run_id(profile_name: str, seed: int) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"normal-{profile_name}-{seed}-{timestamp}-{uuid4().hex[:6]}"


def expected_status(scenario: str) -> int:
    return {
        "valid": 200,
        "invalid_login": 401,
        "invalid_account": 404,
        "invalid_amount": 422,
    }[scenario]


def request_payload(run_id: str, item: PlannedRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "username": "demo",
        "password": "faultweave-demo",
        "account_number": "FW-DEMO-001",
        "amount_minor": item.amount_minor,
        "currency": "INR",
        "recipient": f"experiment-{run_id[-20:]}-{item.sequence:05d}",
    }
    if item.scenario == "invalid_login":
        payload["password"] = "invalid-demo-password"
    elif item.scenario == "invalid_account":
        payload["account_number"] = "FW-NOT-FOUND"
    elif item.scenario == "invalid_amount":
        payload["amount_minor"] = 0
    return payload


async def execute_request(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    gateway_url: str,
    run_id: str,
    seed: int,
    item: PlannedRequest,
) -> dict[str, Any]:
    request_id = f"{run_id}-request-{item.sequence:05d}"
    trace_id = str(uuid5(NAMESPACE_URL, f"faultweave:{run_id}:{seed}:{item.sequence}"))
    started_at = datetime.now(UTC)
    started_clock = time.perf_counter()
    status_code: int | None = None
    response_body: dict[str, Any] = {}
    transport_error: str | None = None
    async with semaphore:
        try:
            response = await client.post(
                f"{gateway_url}/api/v1/transactions",
                headers={
                    "X-Run-ID": run_id,
                    "X-Request-ID": request_id,
                    "X-Trace-ID": trace_id,
                },
                json=request_payload(run_id, item),
            )
            status_code = response.status_code
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    response_body = parsed
            except json.JSONDecodeError:
                response_body = {}
        except httpx.RequestError as exc:
            transport_error = type(exc).__name__
    latency_ms = round((time.perf_counter() - started_clock) * 1000, 3)
    expected = status_code == expected_status(item.scenario)
    if item.scenario == "valid":
        expected = expected and response_body.get("status") == "COMPLETED"
    return {
        "sequence": item.sequence,
        "scheduled_offset_seconds": round(item.offset_seconds, 6),
        "scenario": item.scenario,
        "request_id": request_id,
        "trace_id": trace_id,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "ended_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "latency_ms": latency_ms,
        "expected_status_code": expected_status(item.scenario),
        "status_code": status_code,
        "expected_outcome": expected,
        "transport_error": transport_error,
        "transaction_id": response_body.get("transaction_id"),
        "payment_id": response_body.get("payment_id"),
    }


async def execute_plan(
    plan: list[PlannedRequest],
    gateway_url: str,
    run_id: str,
    seed: int,
    max_concurrency: int,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max_concurrency)
    limits = httpx.Limits(
        max_connections=max_concurrency,
        max_keepalive_connections=max(10, max_concurrency // 2),
    )
    tasks: list[asyncio.Task[dict[str, Any]]] = []
    start_clock = time.perf_counter()
    async with httpx.AsyncClient(timeout=15.0, limits=limits) as client:
        for item in plan:
            delay = item.offset_seconds - (time.perf_counter() - start_clock)
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(
                asyncio.create_task(
                    execute_request(client, semaphore, gateway_url, run_id, seed, item)
                )
            )
        results = await asyncio.gather(*tasks)
    return sorted(results, key=lambda result: result["sequence"])


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


def collect_run_events(run_id: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "--since", "10m"],
        cwd=PROJECT_DIRECTORY,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Docker log collection failed")
    events = [event for line in result.stdout.splitlines() if (event := extract_event(line))]
    return sorted(
        (event for event in events if event.get("run_id") == run_id),
        key=lambda event: event["timestamp"],
    )


def wait_for_run_events(run_id: str, expected_request_ids: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(10):
        events = collect_run_events(run_id)
        observed = {event.get("request_id") for event in events}
        if expected_request_ids.issubset(observed):
            return events
        time.sleep(0.5)
    return events


def load_expected_edges() -> set[tuple[str, str]]:
    with TOPOLOGY_PATH.open(encoding="utf-8") as stream:
        topology = json.load(stream)
    return {(edge["source"], edge["target"]) for edge in topology["edges"]}


def validate_correlated_run(
    request_results: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    events_by_request: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_request.setdefault(str(event.get("request_id")), []).append(event)
    expected_edges = load_expected_edges()
    all_services = {"gateway", "authentication", "account", "transaction", "payment", "ledger"}

    for result in request_results:
        request_id = result["request_id"]
        related = events_by_request.get(request_id, [])
        if not related:
            errors.append(f"no events for {request_id}")
            continue
        if any(event.get("trace_id") != result["trace_id"] for event in related):
            errors.append(f"trace mismatch for {request_id}")
        if result["scenario"] == "valid":
            services = {event.get("service") for event in related}
            if services != all_services:
                errors.append(f"incomplete valid-service path for {request_id}")
            edges = {
                (event.get("service"), event.get("downstream_service"))
                for event in related
                if event.get("event_type") == "downstream_request_completed"
            }
            if not expected_edges.issubset(edges):
                errors.append(f"incomplete dependency path for {request_id}")
            if any(event.get("success") is False for event in related):
                errors.append(f"failed event in valid request {request_id}")
    return list(dict.fromkeys(errors))


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize(
    results: list[dict[str, Any]],
    actual_duration: float,
    correlated_event_count: int,
) -> RunSummary:
    latencies = [float(result["latency_ms"]) for result in results]
    statuses = Counter(result["status_code"] for result in results if result["status_code"])
    scenarios = Counter(str(result["scenario"]) for result in results)
    completed_responses = sum(result["status_code"] is not None for result in results)
    expected_outcomes = sum(bool(result["expected_outcome"]) for result in results)
    completed_transactions = sum(
        result["scenario"] == "valid" and result["status_code"] == 200
        for result in results
    )
    return RunSummary(
        scheduled_requests=len(results),
        completed_responses=completed_responses,
        expected_outcomes=expected_outcomes,
        completed_transactions=completed_transactions,
        unexpected_outcomes=len(results) - expected_outcomes,
        transport_errors=sum(result["transport_error"] is not None for result in results),
        server_errors=sum(
            result["status_code"] is not None and result["status_code"] >= 500
            for result in results
        ),
        response_rate=completed_responses / len(results),
        success_rate=sum(
            result["status_code"] is not None and 200 <= result["status_code"] < 300
            for result in results
        )
        / len(results),
        expected_outcome_rate=expected_outcomes / len(results),
        completed_transaction_rate=completed_transactions / len(results),
        achieved_rps=len(results) / actual_duration,
        status_counts=[
            StatusCount(status_code=status, count=count)
            for status, count in sorted(statuses.items())
        ],
        scenario_counts=dict(sorted(scenarios.items())),
        latency=LatencySummary(
            minimum_ms=min(latencies),
            p50_ms=percentile(latencies, 0.50),
            p95_ms=percentile(latencies, 0.95),
            p99_ms=percentile(latencies, 0.99),
            maximum_ms=max(latencies),
        ),
        correlated_event_count=correlated_event_count,
    )


def current_git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_DIRECTORY,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def run_experiment(
    profile_name: str,
    seed: int,
    *,
    duration_seconds: float | None = None,
    target_rps: float | None = None,
    gateway_url: str | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    max_concurrency: int = 100,
    run_id: str | None = None,
    purpose: str = "ad_hoc",
) -> ExperimentManifest:
    profile = get_profile(profile_name)
    duration = duration_seconds or profile.duration_seconds
    rps = target_rps or profile.target_rps
    actual_run_id = run_id or make_run_id(profile_name, seed)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,75}", actual_run_id):
        raise ValueError(
            "run ID must be at most 75 characters and contain only letters, digits, "
            "underscores, and hyphens"
        )
    actual_gateway_url = gateway_url or os.getenv(
        "FAULTWEAVE_GATEWAY_URL", "http://localhost:18110"
    )
    plan = build_plan(profile, seed, duration, rps)
    started_at = datetime.now(UTC)
    started_clock = time.perf_counter()
    results = asyncio.run(
        execute_plan(
            plan,
            actual_gateway_url,
            actual_run_id,
            seed,
            max_concurrency,
        )
    )
    actual_duration = time.perf_counter() - started_clock
    ended_at = datetime.now(UTC)

    run_directory = output_root / "raw" / actual_run_id
    requests_path = run_directory / "requests.jsonl"
    events_path = run_directory / "events.jsonl"
    write_jsonl(requests_path, results)
    expected_ids = {result["request_id"] for result in results}
    events = wait_for_run_events(actual_run_id, expected_ids)
    write_jsonl(events_path, events)

    correlation_errors = validate_correlated_run(results, events)
    summary = summarize(results, actual_duration, len(events))
    if summary.unexpected_outcomes or summary.transport_errors or summary.server_errors:
        correlation_errors.append("run contains unexpected request outcomes")
    if correlation_errors:
        raise RuntimeError("; ".join(correlation_errors[:10]))

    manifest = ExperimentManifest(
        run_id=actual_run_id,
        purpose=purpose,
        profile=profile.name,
        seed=seed,
        configured_duration_seconds=duration,
        actual_duration_seconds=actual_duration,
        target_rps=rps,
        rate_segments=[
            {"fraction": segment.fraction, "multiplier": segment.multiplier}
            for segment in profile.rate_segments
        ],
        started_at=started_at,
        ended_at=ended_at,
        gateway_url=actual_gateway_url,
        git_commit=current_git_commit(),
        summary=summary,
        artifacts=RunArtifacts(
            requests_path=requests_path.relative_to(PROJECT_DIRECTORY).as_posix(),
            events_path=events_path.relative_to(PROJECT_DIRECTORY).as_posix(),
            requests_sha256=sha256_file(requests_path),
            events_sha256=sha256_file(events_path),
        ),
    )
    manifest_path = output_root / "manifests" / f"{actual_run_id}.json"
    write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a seeded normal FaultWeave experiment")
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--rps", type=float)
    parser.add_argument("--gateway-url")
    parser.add_argument("--max-concurrency", type=int, default=100)
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")
    if args.rps is not None and args.rps <= 0:
        parser.error("--rps must be positive")
    if args.max_concurrency <= 0:
        parser.error("--max-concurrency must be positive")
    return args


def main() -> int:
    args = parse_args()
    if args.dry_run:
        profile = get_profile(args.profile)
        duration = args.duration or profile.duration_seconds
        rps = args.rps or profile.target_rps
        plan = build_plan(profile, args.seed, duration, rps)
        scenarios = Counter(item.scenario for item in plan)
        print(f"PASS: Planned {len(plan)} requests without sending traffic")
        print(f"Profile: {profile.name}")
        print(f"Seed: {args.seed}")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Base target: {rps:.2f} req/s")
        print(f"Scenarios: {dict(sorted(scenarios.items()))}")
        return 0
    try:
        manifest = run_experiment(
            args.profile,
            args.seed,
            duration_seconds=args.duration,
            target_rps=args.rps,
            gateway_url=args.gateway_url,
            max_concurrency=args.max_concurrency,
            run_id=args.run_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"PASS: Normal experiment {manifest.run_id} completed")
    print(f"Profile: {manifest.profile}")
    print(f"Requests: {manifest.summary.scheduled_requests}")
    print(f"Expected outcome rate: {manifest.summary.expected_outcome_rate:.3f}")
    print(f"Achieved throughput: {manifest.summary.achieved_rps:.2f} req/s")
    print(f"p95 latency: {manifest.summary.latency.p95_ms:.2f} ms")
    print(f"Correlated events: {manifest.summary.correlated_event_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
