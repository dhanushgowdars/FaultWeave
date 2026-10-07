from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from experiments.faults.catalog import CORE_KNOWN_FAULTS, EXTENDED_KNOWN_FAULTS  # noqa: E402
from experiments.faults.lifecycle import FaultStateStore  # noqa: E402
from scripts.live_demo_cases import (  # noqa: E402
    CONTROL_DIRECTORY,
    SCENARIOS,
    DemoCaseStore,
    inject_case,
    remediate_case,
    run_case_transaction,
    safety_cleanup_case,
    verify_case_recovery,
)


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_id: str
    confirmation: Literal["INJECT_CONTROLLED_FAULT"]


class TransactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["SEND_TEST_TRANSACTION"]


class RemediationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["APPROVE_REMEDIATION"]
    approved_by: str = Field(min_length=2, max_length=80)


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["VERIFY_RECOVERY"]


class DemoController:
    def __init__(self, store: DemoCaseStore | None = None) -> None:
        self.store = store or DemoCaseStore()
        self._lock = threading.RLock()
        self._case_id: str | None = None
        self._watchdog: threading.Timer | None = None

    def _case(self, case_id: str) -> dict[str, object]:
        case = self.store.get(case_id)
        if case is None:
            raise ValueError("case not found")
        if case_id != self._case_id:
            raise RuntimeError("this case is not the active guided demonstration")
        return case

    def _cancel_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    def _run_watchdog(self, case_id: str) -> None:
        with self._lock:
            case = self.store.get(case_id)
            if case is None:
                return
            safety_cleanup_case(self.store, case)
            self._watchdog = None

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            case = self.store.get(self._case_id) if self._case_id else None
        active = bool(case and case["status"] not in {"RESOLVED", "ABORTED", "FAILED"})
        return {
            "status": case["status"] if case else "IDLE",
            "active": active,
            "case_id": self._case_id,
            "case": case,
        }

    def inject(self, request: DemoRequest) -> dict[str, object]:
        if request.fault_id not in SCENARIOS:
            raise ValueError(f"unsupported demonstration fault: {request.fault_id}")
        with self._lock:
            current = self.snapshot()
            if current["active"]:
                raise RuntimeError("finish or abort the active guided demonstration first")
            if FaultStateStore(CONTROL_DIRECTORY).active() is not None:
                raise RuntimeError("another controlled fault owns the global fault lease")
            case = self.store.create(request.fault_id)
            self._case_id = str(case["case_id"])
            inject_case(self.store, case)
            if case["status"] == "FAULT_ACTIVE":
                self._watchdog = threading.Timer(
                    270,
                    self._run_watchdog,
                    args=(self._case_id,),
                )
                self._watchdog.daemon = True
                self._watchdog.start()
        return self.snapshot()

    def transact(self, case_id: str) -> dict[str, object]:
        with self._lock:
            case = self._case(case_id)
        try:
            run_case_transaction(self.store, case)
        except Exception as exc:
            case["error"] = str(exc)
            case["status"] = "AWAITING_REVIEW"
            case["review"]["status"] = "PENDING"
            self.store.append(
                case,
                "TRANSACTION_ERROR",
                "Transaction investigation stopped",
                str(exc),
                state="FAILED",
            )
            raise RuntimeError(str(exc)) from exc
        return self.snapshot()

    def remediate(self, case_id: str, approved_by: str) -> dict[str, object]:
        with self._lock:
            case = self._case(case_id)
        remediate_case(self.store, case, approved_by.strip())
        with self._lock:
            if case["status"] == "READY_TO_VERIFY":
                self._cancel_watchdog()
        return self.snapshot()

    def verify(self, case_id: str) -> dict[str, object]:
        with self._lock:
            case = self._case(case_id)
        verify_case_recovery(self.store, case)
        return self.snapshot()


controller = DemoController()
app = FastAPI(title="FaultWeave Guided Demo Control", version="5.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ready",
        "api_version": "5.0",
        "scope": "explicit guided incident demonstration",
        "persistence": "sqlite",
    }


@app.get("/catalog")
def catalog() -> dict[str, object]:
    definitions = {**CORE_KNOWN_FAULTS, **EXTENDED_KNOWN_FAULTS}
    faults = []
    for fault_id, scenario in SCENARIOS.items():
        definition = next(item for key, item in definitions.items() if key.value == fault_id)
        faults.append(
            {
                "fault_id": fault_id,
                "tier": definition.tier,
                "mechanism": definition.mechanism,
                "description": definition.description,
                **scenario,
            }
        )
    return {
        "api_version": "5.0",
        "faults": faults,
        "journey": [
            "gateway",
            "authentication",
            "account",
            "transaction",
            "payment",
            "ledger",
        ],
        "case_contract": (
            "inject, transact, investigate, approve, remediate and verify are explicit actions"
        ),
    }


@app.get("/status")
def status() -> dict[str, object]:
    return controller.snapshot()


@app.post("/runs", status_code=202)
def inject(request: DemoRequest) -> dict[str, object]:
    try:
        return controller.inject(request)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/cases/{case_id}/transactions", status_code=202)
def transact(case_id: str, request: TransactionRequest) -> dict[str, object]:
    try:
        return controller.transact(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/cases/{case_id}/remediate", status_code=202)
def remediate(case_id: str, request: RemediationRequest) -> dict[str, object]:
    try:
        return controller.remediate(case_id, request.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/cases/{case_id}/verify", status_code=202)
def verify(case_id: str, request: VerificationRequest) -> dict[str, object]:
    try:
        return controller.verify(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/cases")
def cases(limit: int = 50) -> dict[str, object]:
    bounded = min(max(limit, 1), 100)
    items = controller.store.list(bounded)
    return {"count": len(items), "cases": items}


@app.get("/cases/{case_id}")
def case(case_id: str) -> dict[str, object]:
    document = controller.store.get(case_id)
    if document is None:
        raise HTTPException(status_code=404, detail="case not found")
    return document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the localhost-only FaultWeave guided incident demonstration"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18116)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("demo control must remain bound to localhost")
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
