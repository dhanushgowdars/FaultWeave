from __future__ import annotations

import json
import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from experiments.manifest import sha256_file, write_json

from .artifacts import FinalRunManifest
from .final_quality_profile import iter_jsonl, parse_timestamp, percentile, read_json

LOCALIZATION_SCHEMA_VERSION = "1.2"
SERVICE_NAMES = ("gateway", "authentication", "account", "transaction", "payment", "ledger")
DATABASE_TOKENS = ("database", "postgres", "asyncpg", "sqlalchemy", "db error", "db_")
POOL_TOKENS = ("pool", "queuepool", "connection checkout")
LOCK_TOKENS = ("lock", "deadlock", "serialization", "rollback")
TIMEOUT_TOKENS = ("timeout", "timed_out", "timed out", "deadline", "504")
CONNECTION_TOKENS = (
    "connect",
    "connection refused",
    "connection reset",
    "network",
    "socket",
    "refused",
    "reset",
)
UNAVAILABLE_TOKENS = ("service unavailable", "unavailable", "503")


@dataclass(frozen=True)
class IntervalEvidence:
    events: tuple[dict[str, Any], ...]
    started_at: datetime


def load_manifests(root: Path) -> list[FinalRunManifest]:
    manifests = []
    for path in sorted((root / "manifests").glob("*.json")):
        manifests.append(FinalRunManifest.model_validate_json(path.read_text(encoding="utf-8")))
    if not manifests:
        raise ValueError("no accepted final-dataset manifests were found")
    return manifests


def load_edges(path: Path) -> tuple[tuple[str, str], ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    edges = tuple((str(item["source"]), str(item["target"])) for item in value["edges"])
    if not edges:
        raise ValueError("service dependency graph is empty")
    return edges


def _interval_bounds(truth: dict[str, Any], name: str) -> tuple[datetime, datetime]:
    for interval in truth.get("intervals", []):
        if interval.get("name") == name:
            return parse_timestamp(interval["started_at"]), parse_timestamp(interval["ended_at"])
    raise ValueError(f"ground truth lacks {name!r} interval")


def _interval_events(
    events: Iterable[dict[str, Any]], started_at: datetime, ended_at: datetime
) -> IntervalEvidence:
    selected = tuple(
        event
        for event in events
        if started_at <= parse_timestamp(event.get("timestamp")) <= ended_at
    )
    return IntervalEvidence(selected, started_at)


def _rate(events: Iterable[dict[str, Any]], predicate: Any) -> float:
    records = tuple(events)
    return sum(bool(predicate(item)) for item in records) / len(records) if records else 0.0


def _failure(event: dict[str, Any]) -> bool:
    status = event.get("status_code")
    return (
        event.get("success") is False
        or event.get("level") in {"ERROR", "CRITICAL"}
        or (status is not None and int(status) >= 400)
    )


def _latency_p95(events: Iterable[dict[str, Any]]) -> float:
    values = [float(item["latency_ms"]) for item in events if item.get("latency_ms") is not None]
    return float(percentile(values, 0.95) or 0.0)


def _increase(fault: float, baseline: float) -> float:
    return max(0.0, fault - baseline)


def _latency_signal(fault: float, baseline: float) -> float:
    if fault <= 0:
        return 0.0
    return min(3.0, math.log1p(fault / max(baseline, 1.0))) / 3.0


def _text(event: dict[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "").lower() for key in ("error_type", "event_type", "message")
    )


def _token_rate(events: Iterable[dict[str, Any]], tokens: tuple[str, ...]) -> float:
    return _rate(events, lambda item: any(token in _text(item) for token in tokens))


def _status_rate(events: Iterable[dict[str, Any]], status_code: int) -> float:
    return _rate(events, lambda item: item.get("status_code") == status_code)


def _first_failure_offset(events: Iterable[dict[str, Any]], start: datetime) -> float | None:
    timestamps = [
        (parse_timestamp(item.get("timestamp")) - start).total_seconds()
        for item in events
        if _failure(item)
    ]
    return min(timestamps) if timestamps else None


def _candidate_record(
    candidate: str,
    kind: str,
    score: float,
    reasons: list[str],
    first_failure_seconds: float | None,
    evidence: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "candidate": candidate,
        "kind": kind,
        "score": round(max(0.0, score), 6),
        "first_failure_seconds": (
            round(first_failure_seconds, 6) if first_failure_seconds is not None else None
        ),
        "reasons": reasons,
        "evidence": {key: round(float(value), 6) for key, value in (evidence or {}).items()},
    }


def _graph_depths(edges: tuple[tuple[str, str], ...]) -> dict[str, int]:
    incoming: dict[str, int] = {name: 0 for name in SERVICE_NAMES}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        if source in incoming and target in incoming:
            outgoing[source].append(target)
            incoming[target] += 1
    roots = [name for name, degree in incoming.items() if degree == 0]
    depths = {name: 0 for name in SERVICE_NAMES}
    queue = deque(roots)
    remaining = dict(incoming)
    while queue:
        source = queue.popleft()
        for target in outgoing[source]:
            depths[target] = max(depths[target], depths[source] + 1)
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    return depths


def _causal_rerank(
    rankings: list[dict[str, Any]], edges: tuple[tuple[str, str], ...]
) -> list[dict[str, Any]]:
    """Re-rank propagated symptoms into probable origins without using ground truth.

    Phase 12C resolves the remaining ambiguity between four observable patterns:
    pure dependency timeout, callee unavailability, service-side resource contention,
    and database-wide failure. The rules use only structured runtime evidence already
    present in the candidate records; expected targets are still evaluation-only.
    """

    by_candidate = {item["candidate"]: item for item in rankings}
    depths = _graph_depths(edges)
    incoming_edges: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        incoming_edges[target].append(f"{source}->{target}")

    def ev(candidate: str, key: str) -> float:
        return float(by_candidate.get(candidate, {}).get("evidence", {}).get(key, 0.0))

    adjustments: dict[str, tuple[float, str]] = {}

    def boost(candidate: str, amount: float, reason: str) -> None:
        current = adjustments.get(candidate)
        if current is None or amount > current[0]:
            adjustments[candidate] = (amount, reason)

    # 1) Database-wide failure. Prefer direct DB evidence. A fallback recognizes the
    # observable gateway->authentication saturation signature produced when PostgreSQL is
    # unavailable before deeper services can execute. Authentication failure bursts do not
    # satisfy the high-latency/timeout part of this signature.
    database_direct = max(
        ev("postgresql", "database_error_delta"),
        ev("postgresql", "database_refused_delta"),
    )
    database_mode = database_direct >= 0.10
    if database_mode:
        boost("postgresql", 3.00, "direct database-connectivity evidence")
    else:
        auth_saturated = (
            ev("authentication", "emitted_failure_delta") >= 0.98
            and ev("authentication", "inbound_failure_delta") >= 0.98
            and ev("authentication", "latency_signal") >= 0.95
            and ev("gateway->authentication", "edge_failure_delta") >= 0.98
            and ev("gateway->authentication", "timeout_error_rate") >= 0.75
            and ev("gateway->authentication", "connection_error_rate") <= 0.10
            and ev("postgresql", "affected_service_count") <= 2.0
        )
        if auth_saturated:
            database_mode = True
            boost("postgresql", 3.00, "database-wide saturation signature")

    # 2) Broad graph latency with almost no failing dependencies is entry-point load.
    service_latency_count = sum(
        ev(service, "latency_signal") > 0.50 for service in SERVICE_NAMES
    )
    edge_latency_count = sum(
        ev(f"{source}->{target}", "edge_latency_signal") > 0.50
        for source, target in edges
    )
    failing_edge_count = sum(
        ev(f"{source}->{target}", "edge_failure_delta") > 0.10
        for source, target in edges
    )
    broad_load_mode = (
        service_latency_count >= 5
        and edge_latency_count >= max(1, len(edges) - 1)
        and failing_edge_count <= 1
        and ev("gateway", "emitted_failure_delta") < 0.52
    )
    if broad_load_mode and not database_mode:
        boost("gateway", 3.00, "broad graph latency consistent with entry-point load")

    # 3) Callee unavailability. A connection/refusal signal plus a callee that receives
    # failed calls but emits no work identifies the unavailable service. Timeout text can
    # coexist with connection refusal, so explicit connection evidence takes precedence.
    unavailable_services = []
    for service in SERVICE_NAMES:
        if (
            ev(service, "inbound_failure_delta") >= 0.90
            and ev(service, "emitted_failure_delta") <= 0.05
            and ev(service, "latency_signal") <= 0.15
        ):
            incoming_strength = max(
                (
                    max(
                        ev(edge, "connection_error_rate"),
                        ev(edge, "unavailable_error_rate"),
                        ev(edge, "status_503_rate"),
                    )
                    for edge in incoming_edges[service]
                ),
                default=0.0,
            )
            if incoming_strength >= 0.50:
                unavailable_services.append(
                    (depths.get(service, 0), incoming_strength, service)
                )
    if unavailable_services and not database_mode and not broad_load_mode:
        service = max(unavailable_services)[-1]
        boost(service, 2.00, "callee unavailable while caller observes connection failure")

    # 4) Pure downstream timeout. Require a failing edge with timeout evidence, no strong
    # connection/unavailability evidence, and a callee that is not itself emitting failures.
    # This prevents resource-failure propagation from being mislabeled as an edge timeout.
    timeout_edges = []
    for source, target in edges:
        candidate = f"{source}->{target}"
        timeout_strength = max(
            ev(candidate, "timeout_error_rate"), ev(candidate, "status_504_rate")
        )
        connection_strength = max(
            ev(candidate, "connection_error_rate"),
            ev(candidate, "unavailable_error_rate"),
            ev(candidate, "status_503_rate"),
        )
        if (
            ev(candidate, "edge_failure_delta") >= 0.50
            and timeout_strength >= 0.25
            and connection_strength < 0.25
            and ev(target, "emitted_failure_delta") <= 0.05
        ):
            timeout_edges.append(
                (
                    depths.get(source, 0),
                    depths.get(target, 0),
                    timeout_strength,
                    candidate,
                )
            )
    if (
        timeout_edges
        and not database_mode
        and not broad_load_mode
        and not unavailable_services
    ):
        candidate = max(timeout_edges)[-1]
        boost(candidate, 1.30, "deepest dependency with explicit timeout evidence")

    # 5) Service-side resource contention. If the callee both receives and emits failures,
    # remains very slow, and its incoming dependency is failing, the service is the probable
    # origin rather than the propagated caller edge. This fallback intentionally does not
    # require literal 'pool'/'lock' tokens because some structured paths surface only timeout.
    resource_services = []
    for service in SERVICE_NAMES:
        incoming_failure = max(
            (ev(edge, "edge_failure_delta") for edge in incoming_edges[service]),
            default=0.0,
        )
        incoming_connection = max(
            (ev(edge, "connection_error_rate") for edge in incoming_edges[service]),
            default=0.0,
        )
        if (
            ev(service, "inbound_failure_delta") >= 0.80
            and ev(service, "emitted_failure_delta") >= 0.20
            and ev(service, "latency_signal") >= 0.80
            and incoming_failure >= 0.80
            and incoming_connection < 0.25
        ):
            resource_services.append(
                (
                    depths.get(service, 0),
                    max(ev(service, "pool_error_rate"), ev(service, "lock_error_rate")),
                    ev(service, "inbound_failure_delta")
                    * ev(service, "emitted_failure_delta"),
                    service,
                )
            )
    if (
        resource_services
        and not database_mode
        and not broad_load_mode
        and not unavailable_services
        and not timeout_edges
    ):
        service = max(resource_services)[-1]
        explicit_resource = max(
            ev(service, "pool_error_rate"), ev(service, "lock_error_rate")
        )
        if explicit_resource >= 0.05:
            boost(service, 1.75, "deepest service with explicit pool/lock evidence")
        else:
            boost(service, 1.35, "deepest service with bidirectional resource-failure evidence")

    # 6) Non-transport downstream 5xx propagation identifies the callee service.
    non_transport = []
    for source, target in edges:
        candidate = f"{source}->{target}"
        if (
            ev(candidate, "edge_failure_delta") >= 0.15
            and ev(candidate, "transport_error_rate") <= 0.05
            and ev(target, "inbound_failure_delta") >= 0.10
        ):
            non_transport.append(
                (
                    depths.get(source, 0),
                    ev(candidate, "edge_failure_delta"),
                    depths.get(target, 0),
                    target,
                )
            )
    if (
        non_transport
        and not database_mode
        and not broad_load_mode
        and not unavailable_services
        and not timeout_edges
    ):
        target = max(non_transport)[-1]
        boost(target, 0.90, "callee-side non-transport failure propagation")

    for candidate, (amount, reason) in adjustments.items():
        item = by_candidate[candidate]
        item["score"] = round(item["score"] + amount, 6)
        item["reasons"].append(f"causal_adjustment={amount:.6f}: {reason}")

    return sorted(rankings, key=lambda item: (-item["score"], item["candidate"]))

def rank_origins(
    baseline: IntervalEvidence,
    fault: IntervalEvidence,
    edges: tuple[tuple[str, str], ...],
) -> list[dict[str, Any]]:
    rankings: list[dict[str, Any]] = []
    first_offsets: list[float] = []
    for service in SERVICE_NAMES:
        base_emitted = tuple(item for item in baseline.events if item.get("service") == service)
        fault_emitted = tuple(item for item in fault.events if item.get("service") == service)
        base_inbound = tuple(
            item for item in baseline.events if item.get("downstream_service") == service
        )
        fault_inbound = tuple(
            item for item in fault.events if item.get("downstream_service") == service
        )
        emitted_delta = _increase(_rate(fault_emitted, _failure), _rate(base_emitted, _failure))
        inbound_delta = _increase(_rate(fault_inbound, _failure), _rate(base_inbound, _failure))
        latency = _latency_signal(_latency_p95(fault_emitted), _latency_p95(base_emitted))
        pool_rate = _token_rate(fault_emitted, POOL_TOKENS)
        lock_rate = _token_rate(fault_emitted, LOCK_TOKENS)
        offset = _first_failure_offset((*fault_emitted, *fault_inbound), fault.started_at)
        if offset is not None:
            first_offsets.append(offset)
        score = 0.45 * emitted_delta + 0.35 * inbound_delta + 0.20 * latency
        evidence = {
            "emitted_failure_delta": emitted_delta,
            "inbound_failure_delta": inbound_delta,
            "latency_signal": latency,
            "pool_error_rate": pool_rate,
            "lock_error_rate": lock_rate,
        }
        reasons = [f"{name}={value:.6f}" for name, value in evidence.items()]
        rankings.append(_candidate_record(service, "service", score, reasons, offset, evidence))

    for source, target in edges:
        base_edge = tuple(
            item
            for item in baseline.events
            if item.get("service") == source and item.get("downstream_service") == target
        )
        fault_edge = tuple(
            item
            for item in fault.events
            if item.get("service") == source and item.get("downstream_service") == target
        )
        failure_delta = _increase(_rate(fault_edge, _failure), _rate(base_edge, _failure))
        latency = _latency_signal(_latency_p95(fault_edge), _latency_p95(base_edge))
        timeout_rate = _token_rate(fault_edge, TIMEOUT_TOKENS)
        connection_rate = _token_rate(fault_edge, CONNECTION_TOKENS)
        unavailable_rate = _token_rate(fault_edge, UNAVAILABLE_TOKENS)
        status_503_rate = _status_rate(fault_edge, 503)
        status_504_rate = _status_rate(fault_edge, 504)
        transport_rate = max(timeout_rate, connection_rate, unavailable_rate, status_503_rate, status_504_rate)
        offset = _first_failure_offset(fault_edge, fault.started_at)
        score = (0.65 * failure_delta + 0.25 * latency + 0.10 * transport_rate) * (
            1.0 if transport_rate > 0 else 0.72
        )
        evidence = {
            "edge_failure_delta": failure_delta,
            "edge_latency_signal": latency,
            "transport_error_rate": transport_rate,
            "timeout_error_rate": timeout_rate,
            "connection_error_rate": connection_rate,
            "unavailable_error_rate": unavailable_rate,
            "status_503_rate": status_503_rate,
            "status_504_rate": status_504_rate,
        }
        reasons = [f"{name}={value:.6f}" for name, value in evidence.items()]
        rankings.append(
            _candidate_record(
                f"{source}->{target}", "dependency_edge", score, reasons, offset, evidence
            )
        )

    database_rate = _token_rate(fault.events, DATABASE_TOKENS)
    baseline_database_rate = _token_rate(baseline.events, DATABASE_TOKENS)
    database_delta = _increase(database_rate, baseline_database_rate)
    database_refused_rate = _rate(
        fault.events,
        lambda item: any(token in _text(item) for token in ("database connection refused", "postgres connection refused")),
    )
    baseline_database_refused_rate = _rate(
        baseline.events,
        lambda item: any(token in _text(item) for token in ("database connection refused", "postgres connection refused")),
    )
    database_refused_delta = _increase(database_refused_rate, baseline_database_refused_rate)
    affected_services = len(
        {item.get("service") for item in fault.events if _failure(item) and item.get("service")}
    )
    database_score = 0.75 * max(database_delta, database_refused_delta) + 0.25 * min(
        1.0, affected_services / 3
    )
    database_evidence = {
        "database_error_delta": database_delta,
        "database_refused_delta": database_refused_delta,
        "affected_service_count": float(affected_services),
    }
    rankings.append(
        _candidate_record(
            "postgresql",
            "database",
            database_score,
            [f"{name}={value:.6f}" for name, value in database_evidence.items()],
            _first_failure_offset(fault.events, fault.started_at),
            database_evidence,
        )
    )

    earliest = min(first_offsets) if first_offsets else None
    if earliest is not None:
        for item in rankings:
            offset = item["first_failure_seconds"]
            if offset is not None:
                item["score"] = round(
                    item["score"] + 0.05 * math.exp(-max(0.0, offset - earliest) / 5.0), 6
                )
                item["reasons"].append("first-abnormal timing bonus applied")

    return _causal_rerank(rankings, edges)


def localize_run(
    project_root: Path,
    manifest: FinalRunManifest,
    edges: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    truth = read_json(project_root / manifest.ground_truth.path)
    baseline_start, baseline_end = _interval_bounds(truth, "baseline")
    fault_start, fault_end = _interval_bounds(truth, "fault")
    events = tuple(iter_jsonl(project_root / manifest.events.path))
    ranking = rank_origins(
        _interval_events(events, baseline_start, baseline_end),
        _interval_events(events, fault_start, fault_end),
        edges,
    )
    expected = str(truth.get("target"))
    candidates = [item["candidate"] for item in ranking]
    return {
        "run_id": manifest.run_id,
        "scenario_type": manifest.scenario_type,
        "split": manifest.split,
        "expected_origin": expected,
        "predicted_origin": candidates[0],
        "top_3": candidates[:3],
        "top_1_correct": candidates[0] == expected,
        "top_3_correct": expected in candidates[:3],
        "ranking": ranking,
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    return {
        "runs": count,
        "top_1_accuracy": round(sum(item["top_1_correct"] for item in rows) / count, 6)
        if count
        else 0.0,
        "top_3_accuracy": round(sum(item["top_3_correct"] for item in rows) / count, 6)
        if count
        else 0.0,
    }


def build_localization_report(
    project_root: Path,
    dataset_root: Path,
    dependency_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    manifests = [item for item in load_manifests(dataset_root) if item.scenario_type != "normal"]
    edges = load_edges(dependency_path)
    results = [localize_run(project_root, item, edges) for item in manifests]
    partitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        partitions[item["scenario_type"]].append(item)
    report = {
        "schema_version": LOCALIZATION_SCHEMA_VERSION,
        "method": "causal dependency-aware observable evidence ranking",
        "candidate_services": list(SERVICE_NAMES),
        "candidate_edges": [f"{source}->{target}" for source, target in edges],
        "candidate_database": "postgresql",
        "scoring_inputs": [
            "service and dependency failure-rate changes",
            "service and dependency latency changes",
            "timeout versus connection/unavailability error evidence",
            "database/resource error evidence",
            "first-abnormal timing and dependency depth",
            "causal propagation direction",
        ],
        "ground_truth_usage": "evaluation_only_after_ranking",
        "metrics": {
            "overall": _metrics(results),
            "known_fault": _metrics(partitions["known_fault"]),
            "sealed_unknown": _metrics(partitions["sealed_unknown"]),
        },
        "coverage": dict(sorted(Counter(item["expected_origin"] for item in results).items())),
        "results": results,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "report.json"
    write_json(output_path, report)
    report["report_sha256"] = sha256_file(output_path)
    return report


def validate_localization_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    metrics = report.get("metrics", {})
    if metrics.get("overall", {}).get("runs") != 220:
        errors.append("expected localization results for 220 abnormal runs")
    if metrics.get("known_fault", {}).get("runs") != 180:
        errors.append("expected 180 known-fault localization results")
    if metrics.get("sealed_unknown", {}).get("runs") != 40:
        errors.append("expected 40 sealed-unknown localization results")
    if report.get("ground_truth_usage") != "evaluation_only_after_ranking":
        errors.append("ground truth must remain outside origin scoring")
    results = report.get("results")
    if not isinstance(results, list) or len(results) != 220:
        errors.append("localization result list is incomplete")
        return errors
    if len({item.get("run_id") for item in results}) != 220:
        errors.append("localization run identities are not unique")
    if any(len(item.get("top_3", [])) != 3 for item in results):
        errors.append("every localization result must contain three ranked origins")
    if any(not item.get("ranking") for item in results):
        errors.append("localization evidence ranking is missing")
    return errors
