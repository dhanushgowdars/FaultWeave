from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SERVICE_NAMES = ("gateway", "authentication", "account", "transaction", "payment", "ledger")
TOPOLOGY_EDGES = (
    ("gateway", "authentication"),
    ("gateway", "account"),
    ("gateway", "transaction"),
    ("transaction", "account"),
    ("transaction", "payment"),
    ("transaction", "ledger"),
    ("payment", "ledger"),
)
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
class LiveIntervalEvidence:
    events: tuple[dict[str, Any], ...]
    started_at: datetime


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"invalid timestamp {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone: {value!r}")
    return parsed.astimezone(UTC)


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


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
    return float(_percentile(values, 0.95) or 0.0)


def _increase(current: float, baseline: float) -> float:
    return max(0.0, current - baseline)


def _latency_signal(current: float, baseline: float) -> float:
    if current <= 0:
        return 0.0
    return min(3.0, math.log1p(current / max(baseline, 1.0))) / 3.0


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
        (_parse_timestamp(item.get("timestamp")) - start).total_seconds()
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
            boost(
                service,
                1.35,
                "deepest service with bidirectional resource-failure evidence",
            )

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


def rank_live_origins(
    baseline: LiveIntervalEvidence,
    current: LiveIntervalEvidence,
    edges: tuple[tuple[str, str], ...] = TOPOLOGY_EDGES,
) -> list[dict[str, Any]]:
    rankings: list[dict[str, Any]] = []
    first_offsets: list[float] = []
    for service in SERVICE_NAMES:
        base_emitted = tuple(item for item in baseline.events if item.get("service") == service)
        current_emitted = tuple(item for item in current.events if item.get("service") == service)
        base_inbound = tuple(
            item for item in baseline.events if item.get("downstream_service") == service
        )
        current_inbound = tuple(
            item for item in current.events if item.get("downstream_service") == service
        )
        emitted_delta = _increase(
            _rate(current_emitted, _failure), _rate(base_emitted, _failure)
        )
        inbound_delta = _increase(
            _rate(current_inbound, _failure), _rate(base_inbound, _failure)
        )
        latency = _latency_signal(
            _latency_p95(current_emitted), _latency_p95(base_emitted)
        )
        pool_rate = _token_rate(current_emitted, POOL_TOKENS)
        lock_rate = _token_rate(current_emitted, LOCK_TOKENS)
        offset = _first_failure_offset(
            (*current_emitted, *current_inbound), current.started_at
        )
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
        rankings.append(
            _candidate_record(service, "service", score, reasons, offset, evidence)
        )

    for source, target in edges:
        base_edge = tuple(
            item
            for item in baseline.events
            if item.get("service") == source and item.get("downstream_service") == target
        )
        current_edge = tuple(
            item
            for item in current.events
            if item.get("service") == source and item.get("downstream_service") == target
        )
        failure_delta = _increase(
            _rate(current_edge, _failure), _rate(base_edge, _failure)
        )
        latency = _latency_signal(_latency_p95(current_edge), _latency_p95(base_edge))
        timeout_rate = _token_rate(current_edge, TIMEOUT_TOKENS)
        connection_rate = _token_rate(current_edge, CONNECTION_TOKENS)
        unavailable_rate = _token_rate(current_edge, UNAVAILABLE_TOKENS)
        status_503_rate = _status_rate(current_edge, 503)
        status_504_rate = _status_rate(current_edge, 504)
        transport_rate = max(
            timeout_rate,
            connection_rate,
            unavailable_rate,
            status_503_rate,
            status_504_rate,
        )
        offset = _first_failure_offset(current_edge, current.started_at)
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
                f"{source}->{target}",
                "dependency_edge",
                score,
                reasons,
                offset,
                evidence,
            )
        )

    database_rate = _token_rate(current.events, DATABASE_TOKENS)
    baseline_database_rate = _token_rate(baseline.events, DATABASE_TOKENS)
    database_delta = _increase(database_rate, baseline_database_rate)
    database_refused_rate = _rate(
        current.events,
        lambda item: any(
            token in _text(item)
            for token in ("database connection refused", "postgres connection refused")
        ),
    )
    baseline_database_refused_rate = _rate(
        baseline.events,
        lambda item: any(
            token in _text(item)
            for token in ("database connection refused", "postgres connection refused")
        ),
    )
    database_refused_delta = _increase(
        database_refused_rate, baseline_database_refused_rate
    )
    affected_services = len(
        {
            item.get("service")
            for item in current.events
            if _failure(item) and item.get("service")
        }
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
            _first_failure_offset(current.events, current.started_at),
            database_evidence,
        )
    )

    earliest = min(first_offsets) if first_offsets else None
    if earliest is not None:
        for item in rankings:
            offset = item["first_failure_seconds"]
            if offset is not None:
                item["score"] = round(
                    item["score"]
                    + 0.05 * math.exp(-max(0.0, offset - earliest) / 5.0),
                    6,
                )
                item["reasons"].append("first-abnormal timing bonus applied")

    return _causal_rerank(rankings, edges)
