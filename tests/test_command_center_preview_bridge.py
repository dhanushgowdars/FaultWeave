"""Verify the preview forwards only the scoped GET route, using no real services."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest

module_path = Path(__file__).resolve().parents[1] / "scripts" / "faultweave_command_center_preview.py"
spec = importlib.util.spec_from_file_location("preview_bridge", module_path)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def unavailable_v2(*args, **kwargs):
    return {"status": "UNAVAILABLE", "source": "isolated_v2_research"}


class GatewayStub(BaseHTTPRequestHandler):
    last_authorization = None
    legacy = False
    feature_age_seconds = 2

    def do_GET(self):
        GatewayStub.last_authorization = self.headers.get("Authorization")
        status = 200
        if self.legacy and self.path == bridge.ROUTE:
            status, data = 404, {"detail": "Not Found"}
        elif self.legacy and self.path == "/api/v1/demo/contract":
            status, data = (200, {"contract_version": "demo"}) if self.headers.get("Authorization") == "Bearer local-test" else (403, {"detail": "Engineer role required"})
        elif self.legacy and self.path == "/api/v1/intelligence/live/features/60":
            status, data = 200, {
                "window_seconds": 60,
                "window_ended_at": (datetime.now(UTC) - timedelta(seconds=self.feature_age_seconds)).isoformat(),
                "request_observation_source": "client_observed",
                "request_count": 12, "event_count": 120, "coverage_seconds": 60,
            }
        elif self.legacy and self.path == "/api/v1/intelligence/incidents/current":
            status, data = 200, {"active": False, "incident": None}
        elif self.legacy and self.path == "/api/v1/intelligence/incidents":
            status, data = 200, {"count": 0, "incidents": []}
        else:
            data = {"schema_version": 1, "telemetry": {"status": "UNOBSERVED"}}
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class CaseGatewayStub(BaseHTTPRequestHandler):
    calls = []
    case_id = "11111111-2222-3333-4444-555555555555"

    def do_POST(self):
        self.send_error(405)

    def do_GET(self):
        CaseGatewayStub.calls.append((self.path, self.headers.get("Authorization")))
        auth = self.headers.get("Authorization")
        if self.path == "/api/v1/demo/contract":
            status, data = (200, {}) if auth == "Bearer engineer" else (403, {})
        elif self.path == "/api/v1/transfers?limit=20&offset=0":
            status, data = 200, {"schema_version": "1.0", "scope": "engineer", "page": {"total": 0, "has_more": False}, "items": []}
        elif self.path == f"/api/v1/transfers/{self.case_id}/evidence":
            status, data = 200, {"schema_version": "1.0", "transfer_id": self.case_id, "timeline": [],
                                 "evidence_sources": {"workflow": "transactions.transfer_workflows"},
                                 "transfer": {}, "case_history": {}}
        elif self.path == "/api/v1/demo/sessions?limit=100":
            status, data = 200, {"items": []}
        else:
            status, data = 404, {"detail": "Not Found"}
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class BridgeTests(unittest.TestCase):
    def test_case_routes_require_engineer_and_only_read_gateway(self):
        CaseGatewayStub.calls = []
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("Existing Site")
            (dist / "faultweave-live.js").write_text("existing adapter")
            with ThreadingHTTPServer(("127.0.0.1", 0), CaseGatewayStub) as gateway:
                with ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(dist, gateway.server_port)) as preview:
                    threads = [Thread(target=s.serve_forever, daemon=True) for s in (gateway, preview)]
                    for thread in threads:
                        thread.start()
                    try:
                        base = f"http://127.0.0.1:{preview.server_port}{bridge.CASES_ROUTE}"
                        with self.assertRaises(HTTPError) as denied:
                            urlopen(base)
                        self.assertEqual(denied.exception.code, 401)
                        with self.assertRaises(HTTPError) as denied:
                            urlopen(Request(base, headers={"Authorization": "Bearer customer"}))
                        self.assertEqual(denied.exception.code, 403)
                        response = urlopen(Request(base, headers={"Authorization": "Bearer engineer"}))
                        self.assertEqual(json.load(response)["items"], [])
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                        case = urlopen(Request(base + "/" + CaseGatewayStub.case_id,
                                               headers={"Authorization": "Bearer engineer"}))
                        self.assertEqual(json.load(case)["source"], "persisted_transfer_evidence")
                        with self.assertRaises(HTTPError) as malformed:
                            urlopen(Request(base + "/../internal/fault", headers={"Authorization": "Bearer engineer"}))
                        self.assertEqual(malformed.exception.code, 404)
                        self.assertEqual(len([row for row in CaseGatewayStub.calls if row[0] == "/api/v1/transfers?limit=20&offset=0"]), 1)
                    finally:
                        for server in (preview, gateway):
                            server.shutdown()
                        for thread in threads:
                            thread.join(timeout=2)

    def test_static_site_and_scoped_proxy(self):
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("Existing Site")
            (dist / "faultweave-live.js").write_text("/* demo adapter */")
            with ThreadingHTTPServer(("127.0.0.1", 0), GatewayStub) as gateway:
                with ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(dist, gateway.server_port, candidate_reader=unavailable_v2)) as preview:
                    threads = [Thread(target=server.serve_forever, daemon=True) for server in (gateway, preview)]
                    for thread in threads:
                        thread.start()
                    try:
                        base = f"http://127.0.0.1:{preview.server_port}"
                        self.assertEqual(urlopen(base).read(), b"Existing Site")
                        route = base + bridge.ROUTE
                        with self.assertRaises(HTTPError) as denied:
                            urlopen(route)
                        self.assertEqual(denied.exception.code, 401)
                        request = Request(route, headers={"Authorization": "Bearer local-test"})
                        response = urlopen(request)
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                        self.assertEqual(json.load(response)["telemetry"]["status"], "UNOBSERVED")
                        self.assertEqual(GatewayStub.last_authorization, "Bearer local-test")
                    finally:
                        for server in (preview, gateway):
                            server.shutdown()
                        for thread in threads:
                            thread.join(timeout=2)

    def test_prepatch_real_gateway_routes_are_adapted_with_engineer_check(self):
        GatewayStub.legacy = True
        candidate_calls = []
        def read_v2(*args, **kwargs):
            candidate_calls.append(True)
            return unavailable_v2(*args, **kwargs)
        try:
            with TemporaryDirectory() as tmp:
                dist = Path(tmp)
                (dist / "index.html").write_text("Existing Site")
                (dist / "faultweave-live.js").write_text("adapter")
                with ThreadingHTTPServer(("127.0.0.1", 0), GatewayStub) as gateway:
                    with ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(dist, gateway.server_port, candidate_reader=read_v2)) as preview:
                        threads = [Thread(target=s.serve_forever, daemon=True) for s in (gateway, preview)]
                        for thread in threads:
                            thread.start()
                        try:
                            route = f"http://127.0.0.1:{preview.server_port}{bridge.ROUTE}"
                            with self.assertRaises(HTTPError) as denied:
                                urlopen(Request(route, headers={"Authorization": "Bearer customer"}))
                            self.assertEqual(denied.exception.code, 403)
                            self.assertEqual(candidate_calls, [])
                            response = urlopen(Request(route, headers={"Authorization": "Bearer local-test"}))
                            snapshot = json.load(response)
                            self.assertEqual(snapshot["telemetry"]["status"], "READY")
                            self.assertEqual(snapshot["telemetry"]["request_count"], 12)
                            self.assertEqual(snapshot["candidate04"]["status"], "UNAVAILABLE")
                            self.assertEqual(len(candidate_calls), 1)
                            GatewayStub.feature_age_seconds = 50
                            stale = json.load(urlopen(Request(route, headers={"Authorization": "Bearer local-test"})))
                            self.assertEqual(stale["telemetry"]["status"], "STALE")
                            self.assertEqual(stale["incident_feed"]["status"], "UNVERIFIED")
                        finally:
                            for server in (preview, gateway):
                                server.shutdown()
                            for thread in threads:
                                thread.join(timeout=2)
        finally:
            GatewayStub.legacy = False
            GatewayStub.feature_age_seconds = 2


if __name__ == "__main__":
    unittest.main()
