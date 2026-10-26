"""Opt-in, isolated V2 research ingress and continuous telemetry observer.

Clients send transaction requests to 127.0.0.1:28120. The proxy forwards them
only to the isolated 28110 gateway, records independent client outcomes, and a
worker correlates those requests with structured isolated Docker events. The
worker posts verified batches to the isolated telemetry API and asks the
frozen research watcher to evaluate newly completed windows. No traffic is
generated here; no fault, model, dataset, or original-stack write is possible.

This is a research ingress, not a public endpoint or a production collector.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime" / "v2-live-observer"
HOST, PORT, UPSTREAM = "127.0.0.1", 28120, 28110
MAX_BODY = 65536
RETENTION_SECONDS = 900


def fail(message: str) -> None:
    raise RuntimeError("STOP: " + message)


def preflight() -> tuple[str, ...]:
    from scripts import faultweave_v2_isolated_rolling_feeder as feeder
    from scripts import faultweave_v2_candidate04_shadow_watch as watcher

    feeder.preflight()  # frozen hash, both stacks, leases, schema, idle buffer
    names = watcher.check_contract()
    if (OUT / "active.lock").exists() or (OUT / "active.lock").is_symlink():
        fail("another observer is active or a previous observer failed; inspect its lock")
    print("PASS: observer read-only preflight; no traffic or inference")
    return names


class ObservationStore:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._lock = threading.Lock()
        self._next = 0
        self.observed: dict[str, dict] = {}
        self.posted_events: dict[str, datetime] = {}
        self.posted_requests: dict[str, datetime] = {}

    def identity(self) -> tuple[str, str]:
        with self._lock:
            self._next += 1
            return f"{self.run_id}-request-{self._next:08d}", str(uuid4())

    def add(self, row: dict) -> None:
        with self._lock:
            rid = row["request_id"]
            if rid in self.observed:
                fail("duplicate client request identity")
            self.observed[rid] = row

    def snapshot(self) -> tuple[dict, set[str], set[str]]:
        with self._lock:
            return dict(self.observed), set(self.posted_events), set(self.posted_requests)

    def commit(self, events: dict[str, datetime], requests: dict[str, datetime]) -> None:
        with self._lock:
            self.posted_events.update(events)
            self.posted_requests.update(requests)
            cutoff = datetime.now(UTC) - timedelta(seconds=RETENTION_SECONDS)
            self.posted_events = {key: at for key, at in self.posted_events.items() if at >= cutoff}
            self.posted_requests = {key: at for key, at in self.posted_requests.items() if at >= cutoff}
            self.observed = {key: row for key, row in self.observed.items()
                             if datetime.fromisoformat(row["started_at"]) >= cutoff}


def project_rows(raw: list[dict], observed: dict[str, dict], run_id: str,
                 posted_events: set[str], posted_requests: set[str]) -> tuple[list[dict], list[dict], dict, dict]:
    """Project only correlated, independently observed data; no ground truth."""
    from scripts import faultweave_v2_isolated_telemetry_bridge as bridge
    from scripts.faultweave_v2_isolated_rolling_feeder import hash_event
    from services.gateway.app.intelligence_live import eligible_runtime_event

    events: list[dict] = []
    event_keys: dict[str, datetime] = {}
    linked: dict[str, set[str]] = {}
    for item in raw:
        if item.get("run_id") != run_id or not eligible_runtime_event(item):
            continue
        rid = item.get("request_id")
        if rid in observed:
            linked.setdefault(rid, set()).add(str(item.get("trace_id") or ""))
        stamp = bridge.utc(item["timestamp"])
        if stamp < datetime.now(UTC) - timedelta(seconds=RETENTION_SECONDS):
            continue
        key = hash_event(item, run_id)
        if key not in posted_events and key not in event_keys:
            events.append(bridge.event_wire(item, run_id))
            event_keys[key] = stamp
    requests: list[dict] = []
    request_keys: dict[str, datetime] = {}
    for rid, row in observed.items():
        if rid in posted_requests or not linked.get(rid):
            continue
        if linked[rid] != {row["trace_id"]}:
            fail("client request and isolated service trace disagree")
        if row["run_id"] != run_id or row["scenario"] != "valid":
            fail("untrusted client request provenance")
        requests.append(bridge.request_wire(row, run_id))
        request_keys[rid] = bridge.utc(row["started_at"])
    return events, requests, event_keys, request_keys


def make_handler(store: ObservationStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/api/v2/observer/status":
                self.send_error(404, "Research status route only")
                return
            observed, events, requests = store.snapshot()
            body = json.dumps({"status": "OBSERVING_ISOLATED_RESEARCH_ONLY",
                               "run_id": store.run_id,
                               "observed_client_requests": len(observed),
                               "ingested_client_requests": len(requests),
                               "ingested_event_identities": len(events),
                               "generates_traffic": False,
                               "fault_control_enabled": False}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/api/v1/transactions":
                self.send_error(404, "Research transaction route only")
                return
            size = self.headers.get("Content-Length", "")
            if not size.isdigit() or not 1 <= int(size) <= MAX_BODY:
                self.send_error(413, "Bounded JSON body required")
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                self.send_error(415, "JSON required")
                return
            try:
                body = self.rfile.read(int(size))
                if not isinstance(json.loads(body), dict):
                    raise ValueError("JSON object required")
            except (ValueError, UnicodeDecodeError):
                self.send_error(400, "Invalid JSON object")
                return
            rid, trace = store.identity()
            began = datetime.now(UTC)
            started = time.monotonic()
            connection = HTTPConnection(HOST, UPSTREAM, timeout=20)
            try:
                headers = {"Content-Type": "application/json", "X-Run-ID": store.run_id,
                           "X-Request-ID": rid, "X-Trace-ID": trace}
                authorization = self.headers.get("Authorization", "")
                if authorization.startswith("Bearer ") and len(authorization) <= 4096:
                    headers["Authorization"] = authorization
                connection.request("POST", self.path, body=body, headers=headers)
                response = connection.getresponse()
                answer = response.read(1024 * 1024 + 1)
                if len(answer) > 1024 * 1024:
                    fail("isolated gateway response too large")
                if response.getheader("X-Request-ID") != rid or response.getheader("X-Trace-ID") != trace:
                    fail("isolated gateway response correlation mismatch")
                status = response.status
                result_type = response.getheader("Content-Type", "application/json")
                latency = round((time.monotonic() - started) * 1000, 3)
                completed = False
                if status == 200:
                    parsed = json.loads(answer)
                    if not isinstance(parsed, dict):
                        fail("isolated gateway returned a non-object success response")
                    completed = parsed.get("status") == "COMPLETED"
                store.add({"run_id": store.run_id, "request_id": rid, "trace_id": trace,
                           "started_at": began.isoformat(), "latency_ms": latency,
                           "status_code": status, "expected_outcome": completed,
                           "transport_error": None, "scenario": "valid"})
                self.send_response(status)
                self.send_header("Content-Type", result_type)
                self.send_header("Content-Length", str(len(answer)))
                self.send_header("X-Request-ID", rid)
                self.send_header("X-Trace-ID", trace)
                self.end_headers()
                self.wfile.write(answer)
            except (OSError, RuntimeError, ValueError):
                # A missing gateway event must never be invented to complete a window.
                self.send_error(503, "Isolated gateway or correlation unavailable")
            finally:
                connection.close()

    return Handler


def collect_once(store: ObservationStore, names: tuple[str, ...]) -> str:
    from scripts.faultweave_v2_final_two_capture import raw_events_for
    from scripts.faultweave_v2_isolated_rolling_feeder import post_once
    from scripts import faultweave_v2_candidate04_shadow_watch as watcher

    observed, seen_events, seen_requests = store.snapshot()
    if not observed:
        return "AWAITING_CLIENT_REQUESTS"
    raw = raw_events_for(store.run_id, True)
    events, requests, event_keys, request_keys = project_rows(
        raw, observed, store.run_id, seen_events, seen_requests)
    post_once(events, requests)  # fail closed on partial ingestion; never retries a POST
    store.commit(event_keys, request_keys)
    return watcher.one_poll(names, infer=True)


def serve() -> None:
    from scripts.faultweave_v2_isolated_telemetry_bridge import write_new

    names = preflight()
    run_id = "v2observer-" + uuid4().hex[:16]
    lock = OUT / "active.lock"
    store = ObservationStore(run_id)
    server = ThreadingHTTPServer((HOST, PORT), make_handler(store))
    write_new(lock, {"run_id": run_id, "started_at": datetime.now(UTC).isoformat(),
                     "research_only": True, "training_eligible": False})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"RESEARCH OBSERVER: http://{HOST}:{PORT}/api/v1/transactions", flush=True)
    print("No traffic generated; no faults enabled; isolated gateway only", flush=True)
    interrupted = False
    try:
        while True:
            time.sleep(5)
            print("OBSERVATION:", collect_once(store, names), flush=True)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if interrupted:
            try:
                collect_once(store, names)
                observed, _, posted = store.snapshot()
                if set(observed) <= posted:
                    lock.unlink()
                    print("PASS: observer stopped after all client observations were ingested")
                else:
                    print("STOP: unposted client observations remain; lock preserved for review")
            except Exception as error:
                print("STOP: final observation failed; lock preserved:", error)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--serve", action="store_true")
    parser.add_argument("--acknowledge-isolated-telemetry-and-research-inference", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight:
        preflight()
    else:
        if not args.acknowledge_isolated_telemetry_and_research_inference:
            fail("research telemetry ingestion and shadow inference require explicit opt-in")
        serve()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("OBSERVER STOP:", error)
        raise SystemExit(2)
