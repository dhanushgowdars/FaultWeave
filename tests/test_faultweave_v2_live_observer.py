"""Offline checks for the isolated research observer; no real traffic or model call."""
from __future__ import annotations

import json
import threading
import unittest
from datetime import UTC, datetime
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from scripts import faultweave_v2_live_observer as observer


class ObserverTests(unittest.TestCase):
    def test_client_rows_require_matching_isolated_trace(self) -> None:
        run = "v2observer-1234567890abcdef"
        rid = run + "-request-00000001"
        now = datetime.now(UTC).isoformat()
        event = {"run_id": run, "request_id": rid, "trace_id": "trace-1",
                 "timestamp": now, "service": "gateway", "event_type": "http_request_completed"}
        request = {"run_id": run, "request_id": rid, "trace_id": "trace-1",
                   "started_at": now, "scenario": "valid"}
        with patch("scripts.faultweave_v2_isolated_rolling_feeder.hash_event", return_value="event-key"), \
             patch("scripts.faultweave_v2_isolated_telemetry_bridge.event_wire", side_effect=lambda row, _: row), \
             patch("scripts.faultweave_v2_isolated_telemetry_bridge.request_wire", side_effect=lambda row, _: row):
            events, requests, event_keys, request_keys = observer.project_rows(
                [event, event], {rid: request}, run, set(), set())
            self.assertEqual((len(events), len(requests)), (1, 1))
            self.assertEqual(set(event_keys), {"event-key"})
            self.assertEqual(set(request_keys), {rid})
            unseen = observer.project_rows([event], {rid: request}, run, set(event_keys), set(request_keys))
            self.assertEqual(unseen[:2], ([], []))
            mismatched = {**request, "trace_id": "wrong"}
            with self.assertRaisesRegex(RuntimeError, "trace disagree"):
                observer.project_rows([event], {rid: mismatched}, run, set(), set())
            self.assertEqual(observer.project_rows([], {rid: request}, run, set(), set())[:2], ([], []))

    def test_proxy_records_independent_response_and_rejects_other_routes(self) -> None:
        class IsolatedGateway(BaseHTTPRequestHandler):
            calls = []
            response_status = 200

            def do_POST(self) -> None:
                self.calls.append((self.path, self.headers["X-Run-ID"],
                                   self.headers["X-Request-ID"], self.headers["X-Trace-ID"]))
                body = (b'{"transfer_id":"demo","status":"COMPLETED"}'
                        if self.response_status == 200 else b'Internal Server Error')
                self.send_response(self.response_status)
                self.send_header("Content-Type", "application/json" if self.response_status == 200 else "text/plain")
                self.send_header("X-Request-ID", self.headers["X-Request-ID"])
                self.send_header("X-Trace-ID", self.headers["X-Trace-ID"])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), IsolatedGateway)
        proxy_store = observer.ObservationStore("v2observer-1234567890abcdef")
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), observer.make_handler(proxy_store))
        threads = [threading.Thread(target=server.serve_forever, daemon=True)
                   for server in (upstream, proxy)]
        for thread in threads:
            thread.start()
        try:
            with patch.object(observer, "UPSTREAM", upstream.server_port):
                conn = HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
                conn.request("POST", "/api/v1/transactions", b'{}',
                             {"Content-Type": "application/json"})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"transfer_id": "demo", "status": "COMPLETED"})
                conn.close()
                IsolatedGateway.response_status = 503
                conn = HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
                conn.request("POST", "/api/v1/transactions", b'{}',
                             {"Content-Type": "application/json"})
                response = conn.getresponse()
                self.assertEqual(response.status, 503)
                self.assertEqual(response.read(), b'Internal Server Error')
                conn.close()
                conn = HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
                conn.request("GET", "/api/v2/observer/status")
                status = conn.getresponse()
                self.assertEqual(status.status, 200)
                self.assertEqual(json.loads(status.read())["observed_client_requests"], 2)
                conn.close()
                conn = HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
                conn.request("POST", "/internal/fault", b'{}',
                             {"Content-Type": "application/json"})
                self.assertEqual(conn.getresponse().status, 404)
                conn.close()
            observed, _, _ = proxy_store.snapshot()
            self.assertEqual(len(observed), 2)
            self.assertEqual(len(IsolatedGateway.calls), 2)
            rows = list(observed.values())
            self.assertEqual(rows[0]["run_id"], proxy_store.run_id)
            self.assertEqual(rows[0]["trace_id"], IsolatedGateway.calls[0][3])
            self.assertTrue(rows[0]["expected_outcome"])
            self.assertFalse(rows[1]["expected_outcome"])
            self.assertEqual(rows[1]["status_code"], 503)
            self.assertGreaterEqual(rows[0]["latency_ms"], 0)
        finally:
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=3)

    def test_no_client_requests_means_no_docker_or_model_io(self) -> None:
        store = observer.ObservationStore("v2observer-1234567890abcdef")
        with patch("scripts.faultweave_v2_final_two_capture.raw_events_for",
                   side_effect=AssertionError("Docker read")), \
             patch("scripts.faultweave_v2_candidate04_shadow_watch.one_poll",
                   side_effect=AssertionError("model inference")):
            self.assertEqual(observer.collect_once(store, ()), "AWAITING_CLIENT_REQUESTS")

    def test_serving_requires_explicit_opt_in(self) -> None:
        with patch.object(observer, "serve", side_effect=AssertionError("started")):
            with self.assertRaisesRegex(RuntimeError, "explicit opt-in"):
                observer.main(["--serve"])


if __name__ == "__main__":
    unittest.main()
