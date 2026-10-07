from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from experiments.faults.adapters import (
    activate_infrastructure_fault,
    deactivate_infrastructure_fault,
    verify_stack_health,
)
from experiments.faults.lifecycle import FaultStateStore
from scripts.collect_logs import extract_event

GATEWAY_URL = "http://localhost:18110"
CASE_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "live-demo"
CONTROL_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "fault-control"

SCENARIOS: dict[str, dict[str, Any]] = {
    "DATABASE_HIGH_LATENCY": {
        "title": "Ledger database slows during payment recording",
        "customer_impact": (
            "The transfer waits while ledger database work exceeds its healthy latency."
        ),
        "target": "ledger",
        "failure_stage": "ledger",
        "cause_component": "Ledger persistence",
        "cause_action": "Adds controlled database latency before the ledger write.",
        "recovery_action": "Remove the latency injection and verify a new transfer.",
        "primary_demo": True,
        "request_count": 10,
    },
    "DATABASE_UNAVAILABLE": {
        "title": "Shared PostgreSQL becomes unavailable",
        "customer_impact": (
            "The first database-backed service cannot finish before the gateway timeout."
        ),
        "target": "postgresql",
        "failure_stage": "authentication",
        "cause_component": "Shared PostgreSQL",
        "cause_action": "Stops the PostgreSQL container before submitting the transaction.",
        "recovery_action": "Restart PostgreSQL, wait for health, then issue a new transaction.",
        "primary_demo": True,
        "request_count": 8,
    },
    "SERVICE_UNAVAILABLE": {
        "title": "Payment service stops during a transfer",
        "customer_impact": (
            "The transaction is created, but the payment dependency cannot be reached."
        ),
        "target": "payment",
        "failure_stage": "payment",
        "cause_component": "Payment service",
        "cause_action": "Stops the payment-service container before the transaction arrives.",
        "recovery_action": "Restart Payment, wait for health, then issue a new transaction.",
        "primary_demo": True,
        "request_count": 8,
    },
    "DOWNSTREAM_TIMEOUT": {
        "title": "Payment dependency times out",
        "customer_impact": (
            "The transaction waits for payment until the downstream timeout is exceeded."
        ),
        "target": "transaction->payment",
        "failure_stage": "payment",
        "cause_component": "Transaction → Payment dependency",
        "cause_action": "Introduces a bounded timeout on the Transaction-to-Payment call.",
        "recovery_action": "Remove the dependency delay and verify a new transaction.",
        "primary_demo": True,
        "request_count": 6,
    },
    "AUTHENTICATION_FAILURE_BURST": {
        "title": "Burst of invalid login attempts",
        "customer_impact": (
            "Invalid credentials are rejected before payment; this is test traffic, "
            "not an authentication outage."
        ),
        "target": "authentication",
        "failure_stage": "authentication",
        "cause_component": "Authentication service",
        "cause_action": "Sends a bounded burst of requests with invalid credentials.",
        "recovery_action": "Stop invalid-login traffic and verify a valid login transaction.",
        "primary_demo": True,
        "request_count": 24,
    },
    "HIGH_LOAD": {
        "title": "Gateway becomes saturated by high request load",
        "customer_impact": (
            "Concurrent transaction requests increase latency and may fail under pressure."
        ),
        "target": "gateway",
        "failure_stage": "gateway",
        "cause_component": "API Gateway",
        "cause_action": (
            "Submits a bounded burst of concurrent requests; actual saturation "
            "must be confirmed from observed latency."
        ),
        "recovery_action": "Stop burst traffic and verify normal request latency.",
        "primary_demo": False,
        "request_count": 60,
    },
    "CONNECTION_POOL_EXHAUSTION": {
        "title": "Transaction database pool is exhausted",
        "customer_impact": "Transaction work waits because database connections are occupied.",
        "target": "transaction",
        "failure_stage": "transaction",
        "cause_component": "Transaction connection pool",
        "cause_action": "Occupies the bounded transaction database connection pool.",
        "recovery_action": "Release held connections and verify a new transaction.",
        "primary_demo": False,
        "request_count": 12,
    },
    "DATABASE_LOCK_CONTENTION": {
        "title": "Payment database work is blocked by a lock",
        "customer_impact": "A conflicting database lock delays payment persistence.",
        "target": "payment",
        "failure_stage": "payment",
        "cause_component": "Payment database lock",
        "cause_action": "Holds a conflicting lock during payment persistence.",
        "recovery_action": "Release the lock and verify payment persistence.",
        "primary_demo": False,
        "request_count": 8,
    },
    "DOWNSTREAM_ERROR_BURST": {
        "title": "Payment service returns an error burst",
        "customer_impact": (
            "The payment dependency returns server errors to the transaction service."
        ),
        "target": "payment",
        "failure_stage": "payment",
        "cause_component": "Payment service",
        "cause_action": "Returns a bounded burst of controlled server errors.",
        "recovery_action": "Disable the error burst and verify a new transaction.",
        "primary_demo": False,
        "request_count": 12,
    },
}


def utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DemoCaseStore:
    def __init__(self, path: Path = CASE_DIRECTORY / "cases.sqlite3") -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS demo_cases (
                    case_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fault_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    document TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _hydrate(document: dict[str, Any]) -> dict[str, Any]:
        """Apply the current presentation contract to older persisted cases."""
        scenario = SCENARIOS.get(document.get("fault_id", ""))
        if scenario is not None:
            document["scenario"] = {**document.get("scenario", {}), **scenario}
        if document.get("status") == "COMPLETED":
            document["status"] = "RESOLVED"
        if document.get("status") == "AUTO_RECOVERED":
            document["status"] = "ABORTED"
        document.setdefault("activation_id", None)
        document.setdefault("fault_injected_at", document.get("started_at"))
        document.setdefault("supporting_requests", [])
        document.setdefault("model_analysis_note", None)
        # Older cases recorded infrastructure readiness as recovery verification.
        # A separate successful customer request is required for that claim.
        document["recovery_verified"] = bool(
            document.get("recovery_result", {}).get("succeeded")
            if isinstance(document.get("recovery_result"), dict)
            else False
        )
        document.setdefault(
            "review",
            {
                "required": False,
                "status": "TIMED_OUT",
                "requested_at": None,
                "approved_at": None,
                "approved_by": None,
                "timeout_seconds": 0,
                "automatic_cleanup": True,
            },
        )
        return document

    def create(self, fault_id: str) -> dict[str, Any]:
        scenario = SCENARIOS[fault_id]
        token = uuid4().hex[:10]
        case_id = f"case-{datetime.now(UTC):%Y%m%dT%H%M%S}-{token[:6]}"
        run_id = f"demo-{token}"
        document: dict[str, Any] = {
            "case_id": case_id,
            "run_id": run_id,
            "request_id": f"{run_id}-request-00001",
            "trace_id": str(uuid4()),
            "fault_id": fault_id,
            "scenario": scenario,
            "status": "QUEUED",
            "phase": "PREPARING",
            "created_at": utc_iso(),
            "started_at": None,
            "finished_at": None,
            "activation_id": None,
            "fault_injected_at": None,
            "transaction_result": None,
            "supporting_requests": [],
            "recovery_result": None,
            "model_analysis": None,
            "model_analysis_note": None,
            "first_failure": None,
            "timeline": [],
            "evidence": [],
            "recovery_verified": False,
            "review": {
                "required": True,
                "status": "NOT_READY",
                "requested_at": None,
                "approved_at": None,
                "approved_by": None,
                "timeout_seconds": 270,
                "automatic_cleanup": False,
            },
            "error": None,
            "evidence_contract": {
                "source": "correlated structured events",
                "selection": (
                    "exact request_id from the gateway buffer with a Docker structured-log fallback"
                ),
                "database": str(self.path),
            },
        }
        now = document["created_at"]
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO demo_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    now,
                    now,
                    document["status"],
                    fault_id,
                    document["request_id"],
                    document["trace_id"],
                    json.dumps(document, separators=(",", ":")),
                ),
            )
        return document

    def get(self, case_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT document FROM demo_cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        return self._hydrate(json.loads(row["document"])) if row else None

    def save(self, document: dict[str, Any]) -> None:
        document["updated_at"] = utc_iso()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE demo_cases
                SET updated_at = ?, status = ?, document = ?
                WHERE case_id = ?
                """,
                (
                    document["updated_at"],
                    document["status"],
                    json.dumps(document, separators=(",", ":")),
                    document["case_id"],
                ),
            )

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT document FROM demo_cases ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._hydrate(json.loads(row["document"])) for row in rows]

    def append(
        self,
        document: dict[str, Any],
        phase: str,
        title: str,
        detail: str,
        *,
        state: str = "COMPLETE",
        service: str | None = None,
        evidence_event_type: str | None = None,
    ) -> None:
        document["phase"] = phase
        document["timeline"].append(
            {
                "sequence": len(document["timeline"]) + 1,
                "timestamp": utc_iso(),
                "phase": phase,
                "state": state,
                "title": title,
                "detail": detail,
                "service": service,
                "evidence_event_type": evidence_event_type,
            }
        )
        self.save(document)


def _transaction(
    run_id: str,
    request_id: str,
    trace_id: str,
    *,
    invalid_login: bool = False,
) -> dict[str, Any]:
    payload = {
        "username": "demo",
        "password": "invalid-demo-password" if invalid_login else "faultweave-demo",
        "account_number": "FW-DEMO-001",
        "amount_minor": 12500,
        "currency": "INR",
        "recipient": "case-demo-merchant",
    }
    started_at = utc_iso()
    started = time.perf_counter()
    try:
        response = httpx.post(
            f"{GATEWAY_URL}/api/v1/transactions",
            headers={
                "X-Run-ID": run_id,
                "X-Request-ID": request_id,
                "X-Trace-ID": trace_id,
            },
            json=payload,
            timeout=15,
        )
        try:
            body: Any = response.json()
        except json.JSONDecodeError:
            body = {"detail": response.text[:500]}
        return {
            "request_id": request_id,
            "trace_id": trace_id,
            "status_code": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "started_at": started_at,
            "ended_at": utc_iso(),
            "succeeded": response.is_success
            and isinstance(body, dict)
            and body.get("status") == "COMPLETED",
            "body": body,
        }
    except httpx.RequestError as exc:
        return {
            "request_id": request_id,
            "trace_id": trace_id,
            "status_code": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "started_at": started_at,
            "ended_at": utc_iso(),
            "succeeded": False,
            "body": {"detail": str(exc)},
            "transport_error": type(exc).__name__,
        }


def _gateway_evidence(request_id: str) -> list[dict[str, Any]]:
    latest: list[dict[str, Any]] = []
    stable_reads = 0
    for _ in range(12):
        try:
            response = httpx.get(
                f"{GATEWAY_URL}/api/v1/intelligence/live/telemetry/recent",
                params={"request_id": request_id, "limit": 500},
                timeout=5,
            )
            response.raise_for_status()
            events = response.json().get("events", [])
            if events:
                ordered = sorted(events, key=lambda item: item["timestamp"])
                if len(ordered) == len(latest):
                    stable_reads += 1
                else:
                    stable_reads = 0
                latest = ordered
                if stable_reads >= 2:
                    break
        except (httpx.HTTPError, KeyError, TypeError):
            pass
        time.sleep(0.75)
    return latest


def _docker_evidence(request_id: str, since: str) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--since", since],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return sorted(
        [
            event
            for line in result.stdout.splitlines()
            if (event := extract_event(line)) and event.get("request_id") == request_id
        ],
        key=lambda item: item["timestamp"],
    )


def _case_evidence(request_id: str, since: str) -> list[dict[str, Any]]:
    events = [*_gateway_evidence(request_id), *_docker_evidence(request_id, since)]
    unique: dict[tuple[object, ...], dict[str, Any]] = {}
    for event in events:
        key = (
            event.get("timestamp"),
            event.get("service"),
            event.get("event_type"),
            event.get("message"),
            event.get("status_code"),
            event.get("latency_ms"),
        )
        unique[key] = event
    return sorted(unique.values(), key=lambda item: item["timestamp"])


def _first_failure(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        status = event.get("status_code")
        if (
            event.get("success") is False
            or event.get("level") in {"ERROR", "CRITICAL"}
            or isinstance(status, int)
            and status >= 400
        ):
            return {
                "timestamp": event.get("timestamp"),
                "service": event.get("service"),
                "event_type": event.get("event_type"),
                "message": event.get("message"),
                "status_code": status,
                "error_type": event.get("error_type"),
                "downstream_service": event.get("downstream_service"),
            }
    return None


def _model_analysis(run_id: str, started_at: str) -> dict[str, Any] | None:
    """Only attach an incident if its API explicitly links it to this run."""
    try:
        httpx.post(f"{GATEWAY_URL}/api/v1/intelligence/live/evaluate", timeout=10)
    except httpx.HTTPError:
        pass
    try:
        response = httpx.get(f"{GATEWAY_URL}/api/v1/intelligence/incidents", timeout=5)
        response.raise_for_status()
        candidates = [
            item
            for item in response.json().get("incidents", [])
            if item.get("detected_at", "") >= started_at
            and run_id in item.get("observed_run_ids", [])
        ]
        return candidates[0] if candidates else None
    except (httpx.HTTPError, KeyError, TypeError):
        return None


def inject_case(store: DemoCaseStore, document: dict[str, Any]) -> dict[str, Any]:
    """Activate one bounded fault without sending customer traffic."""
    scenario = document["scenario"]
    control = FaultStateStore(CONTROL_DIRECTORY)
    document["status"] = "INJECTING"
    document["started_at"] = utc_iso()
    activation = None
    try:
        health = httpx.get(f"{GATEWAY_URL}/health/ready", timeout=5)
        health.raise_for_status()
        store.append(
            document,
            "PRECHECK",
            "Healthy baseline confirmed",
            "The gateway was ready before the controlled fault was introduced.",
            service="gateway",
        )
        activation = control.acquire(
            run_id=document["run_id"],
            fault_id=document["fault_id"],
            target=scenario["target"],
            intensity="medium",
            duration_seconds=300,
        )
        activate_infrastructure_fault(activation)
        document["activation_id"] = activation.activation_id
        document["fault_injected_at"] = utc_iso()
        document["status"] = "FAULT_ACTIVE"
        traffic_fault = document["fault_id"] in {
            "AUTHENTICATION_FAILURE_BURST",
            "HIGH_LOAD",
        }
        store.append(
            document,
            "FAULT_ACTIVE",
            "Traffic scenario is armed" if traffic_fault else "Controlled fault is active",
            (
                (
                    "No burst has been sent yet. "
                    if traffic_fault
                    else f"{scenario['cause_action']} No customer transaction has been sent. "
                )
                + "The next action is controlled by the presenter."
            ),
            state="RUNNING",
            service=scenario["failure_stage"],
        )
    except Exception as exc:
        if activation is not None:
            try:
                deactivate_infrastructure_fault(activation)
            finally:
                control.release(activation.activation_id)
        document["error"] = str(exc)
        document["status"] = "FAILED"
        store.append(
            document,
            "INJECTION_FAILED",
            "Fault injection failed",
            str(exc),
            state="FAILED",
        )
    return document


def run_case_transaction(store: DemoCaseStore, document: dict[str, Any]) -> dict[str, Any]:
    """Run and investigate the original customer transaction while the fault remains active."""
    if document["status"] != "FAULT_ACTIVE":
        raise RuntimeError("the controlled fault must be active before sending a transaction")
    scenario = document["scenario"]
    document["status"] = "TRANSACTION_RUNNING"
    store.append(
        document,
        "TRANSACTION_SUBMITTED",
        "Test transaction submitted",
        "A real correlated transaction entered the API Gateway.",
        state="RUNNING",
        service="gateway",
    )
    invalid_login = document["fault_id"] == "AUTHENTICATION_FAILURE_BURST"
    count = int(scenario["request_count"])
    results: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(count, 24)) as executor:
        futures = {
            executor.submit(
                _transaction,
                document["run_id"],
                (
                    document["request_id"]
                    if index == 1
                    else f"{document['run_id']}-signal-{index:05d}"
                ),
                document["trace_id"] if index == 1 else str(uuid4()),
                invalid_login=invalid_login,
            ): index
            for index in range(1, count + 1)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    document["transaction_result"] = results[1]
    document["supporting_requests"] = [
        {
            key: result[key]
            for key in (
                "request_id",
                "trace_id",
                "status_code",
                "latency_ms",
                "started_at",
                "ended_at",
                "succeeded",
            )
        }
        for index, result in sorted(results.items())
        if index != 1
    ]
    failed = not results[1]["succeeded"]
    store.append(
        document,
        "CUSTOMER_RESULT",
        "Customer transaction failed" if failed else "Customer response received",
        (
            f"Gateway returned HTTP {results[1]['status_code']} after "
            f"{results[1]['latency_ms']:.1f} ms."
        ),
        state="FAILED" if failed else "COMPLETE",
        service=scenario["failure_stage"],
    )
    evidence = _case_evidence(document["request_id"], document["started_at"])
    document["evidence"] = evidence
    document["first_failure"] = _first_failure(evidence)
    document["model_analysis"] = _model_analysis(document["run_id"], document["started_at"])
    if document["model_analysis"] is None:
        document["model_analysis_note"] = (
            "The live incident API did not link a model result to this run. "
            "The request-correlated events below remain available for investigation."
        )
    document["review"].update({"status": "PENDING", "requested_at": utc_iso()})
    document["status"] = "AWAITING_REVIEW"
    store.append(
        document,
        "AWAITING_REVIEW",
        "Investigation ready for engineer review",
        (
            f"Stored {len(evidence)} events for the highlighted request and "
            f"{len(document['supporting_requests'])} supporting request results. "
            "The controlled fault remains active and recovery is not authorized."
        ),
        state="RUNNING",
        service=scenario["failure_stage"],
    )
    return document


def remediate_case(
    store: DemoCaseStore,
    document: dict[str, Any],
    approved_by: str,
) -> dict[str, Any]:
    """Remove the fault after an explicit engineer decision; do not run recovery traffic."""
    if document["status"] != "AWAITING_REVIEW":
        raise RuntimeError("the case is not awaiting engineer review")
    control = FaultStateStore(CONTROL_DIRECTORY)
    activation = control.active()
    if activation is None or activation.activation_id != document.get("activation_id"):
        raise RuntimeError("the active fault lease does not match this case")
    document["status"] = "REMEDIATING"
    document["review"].update(
        {"status": "APPROVED", "approved_at": utc_iso(), "approved_by": approved_by}
    )
    store.append(
        document,
        "REMEDIATION_APPROVED",
        "Engineer approved remediation",
        f"{approved_by} approved: {document['scenario']['recovery_action']}",
        state="RUNNING",
        service=document["scenario"]["failure_stage"],
    )
    try:
        deactivate_infrastructure_fault(activation)
        verify_stack_health()
        control.release(activation.activation_id)
        document["status"] = "READY_TO_VERIFY"
        store.append(
            document,
            "INFRASTRUCTURE_RESTORED",
            "Infrastructure is healthy",
            "The controlled condition was removed. No recovery transaction has run yet.",
            service="gateway",
        )
    except Exception as exc:
        document["error"] = str(exc)
        document["status"] = "FAILED"
        store.append(document, "REMEDIATION_FAILED", "Remediation failed", str(exc), state="FAILED")
    return document


def verify_case_recovery(store: DemoCaseStore, document: dict[str, Any]) -> dict[str, Any]:
    """Prove recovery with a new request identity after remediation."""
    if document["status"] != "READY_TO_VERIFY":
        raise RuntimeError("remediation must complete before recovery verification")
    document["status"] = "VERIFYING"
    store.append(
        document,
        "RECOVERY_TRANSACTION_SUBMITTED",
        "New verification transaction submitted",
        "This request has a different request ID and trace ID from the failed transaction.",
        state="RUNNING",
        service="gateway",
    )
    recovery_id = f"{document['run_id']}-recovery"
    document["recovery_result"] = _transaction(
        recovery_id,
        f"{recovery_id}-request-00001",
        str(uuid4()),
    )
    succeeded = bool(document["recovery_result"]["succeeded"])
    document["recovery_verified"] = succeeded
    document["status"] = "RESOLVED" if succeeded else "FAILED"
    document["finished_at"] = utc_iso()
    store.append(
        document,
        "RESOLVED" if succeeded else "RECOVERY_FAILED",
        "Recovery verified" if succeeded else "Recovery verification failed",
        (
            "The new transaction completed through the restored service path."
            if succeeded
            else "The new transaction did not complete; the case remains unresolved."
        ),
        state="COMPLETE" if succeeded else "FAILED",
        service="gateway",
    )
    return document


def safety_cleanup_case(store: DemoCaseStore, document: dict[str, Any]) -> dict[str, Any]:
    """Remove an abandoned fault without claiming an engineer-approved resolution."""
    if document["status"] not in {"FAULT_ACTIVE", "AWAITING_REVIEW", "FAILED"}:
        return document
    control = FaultStateStore(CONTROL_DIRECTORY)
    activation = control.active()
    if activation is None or activation.activation_id != document.get("activation_id"):
        return document
    try:
        deactivate_infrastructure_fault(activation)
        control.release(activation.activation_id)
        verify_stack_health()
        document["review"].update({"status": "TIMED_OUT", "automatic_cleanup": True})
        document["status"] = "ABORTED"
        document["finished_at"] = utc_iso()
        store.append(
            document,
            "SAFETY_CLEANUP",
            "Case aborted by safety watchdog",
            "The fault was removed for safety; no engineer-approved recovery was claimed.",
            state="FAILED",
            service=document["scenario"]["failure_stage"],
        )
    except Exception as exc:
        document["error"] = str(exc)
        document["status"] = "FAILED"
        store.append(
            document,
            "SAFETY_CLEANUP_FAILED",
            "Safety cleanup failed",
            str(exc),
            state="FAILED",
        )
    return document
