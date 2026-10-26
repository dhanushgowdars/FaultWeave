"""Exercise the local bridge against a stub; no money, faults, or live gateway."""
from __future__ import annotations

import json
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.faultweave_command_center_preview import make_handler, DEMO_SESSIONS, LOGIN_ROUTE


class Stub(BaseHTTPRequestHandler):
    calls = []
    session = {"session_id": "demo-1234567890abcdef", "state": "PREPARED", "mode": "CONTROLLED",
               "scenario": {"fault_id": "SERVICE_UNAVAILABLE"}, "events": []}

    def reply(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.calls.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/api/v1/demo/contract":
            return self.reply(200 if self.headers.get("Authorization") == "Bearer engineer-token" else 403, {})
        if self.path == "/api/v1/demo/scenarios":
            return self.reply(200, {"items": [{"scenario_id": "normal-transfer", "title": "Normal"}]})
        if self.path == DEMO_SESSIONS + "?limit=25":
            return self.reply(200, {"items": [self.session]})
        if self.path == DEMO_SESSIONS + "/demo-1234567890abcdef":
            return self.reply(200, self.session)
        return self.reply(200, {"schema_version": 1, "telemetry": {"status": "UNOBSERVED"}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append(("POST", self.path, self.headers.get("Authorization")))
        if self.path == "/api/v1/auth/login":
            if body.get("username") == "engineer" and body.get("password") == "secret":
                return self.reply(200, {"role": "engineer", "access_token": "engineer-token", "expires_in": 900})
            return self.reply(200, {"role": "customer", "access_token": "customer-token", "expires_in": 900})
        if self.path == DEMO_SESSIONS:
            return self.reply(201, self.session)
        if self.path == DEMO_SESSIONS + "/demo-1234567890abcdef/arm":
            self.session["state"] = "FAULT_ARMED"
            return self.reply(200, self.session)
        return self.reply(404, {"detail": "Not found"})

    def log_message(self, *args):
        pass


class OperationBridgeTests(unittest.TestCase):
    def test_login_cookie_scoped_actions_and_logout(self):
        Stub.calls = []
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("Existing Site")
            (dist / "faultweave-live.js").write_text("Existing adapter")
            with ThreadingHTTPServer(("127.0.0.1", 0), Stub) as gateway:
                with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(dist, gateway.server_port,
                    candidate_reader=lambda *a, **kw: {"status": "UNAVAILABLE"})) as preview:
                    threads = [Thread(target=s.serve_forever, daemon=True) for s in (gateway, preview)]
                    for thread in threads: thread.start()
                    try:
                        base = f"http://127.0.0.1:{preview.server_port}"

                        def request(path, body=None, cookie=None, origin=None):
                            headers = {}
                            if body is not None: headers["Content-Type"] = "application/json"
                            if cookie: headers["Cookie"] = cookie
                            if origin: headers["Origin"] = origin
                            return urlopen(Request(base + path, headers=headers,
                                data=json.dumps(body).encode() if body is not None else None))

                        with self.assertRaises(HTTPError) as denied:
                            request(DEMO_SESSIONS, {})
                        self.assertEqual(denied.exception.code, 401)
                        with self.assertRaises(HTTPError) as denied:
                            request(LOGIN_ROUTE, {"username": "customer", "password": "secret"})
                        self.assertEqual(denied.exception.code, 403)
                        with self.assertRaises(HTTPError) as denied:
                            request(LOGIN_ROUTE, {"username": "engineer", "password": "secret"}, origin="https://outside.example")
                        self.assertEqual(denied.exception.code, 403)
                        response = request(LOGIN_ROUTE, {"username": "engineer", "password": "secret"})
                        self.assertEqual(json.load(response)["role"], "engineer")
                        self.assertNotIn("engineer-token", str(response.headers))
                        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
                        self.assertEqual(json.load(request("/api/v1/command-center/session", cookie=cookie))["connected"], True)
                        self.assertEqual(json.load(request("/api/v1/demo/scenarios", cookie=cookie))["items"][0]["scenario_id"], "normal-transfer")
                        self.assertEqual(json.load(request(DEMO_SESSIONS + "?limit=25", cookie=cookie))["items"][0]["state"], "PREPARED")
                        self.assertEqual(json.load(request(DEMO_SESSIONS, {"mode": "CONTROLLED"}, cookie=cookie))["session_id"], Stub.session["session_id"])
                        path = DEMO_SESSIONS + "/demo-1234567890abcdef/arm"
                        self.assertEqual(json.load(request(path, {}, cookie))["state"], "FAULT_ARMED")
                        with self.assertRaises(HTTPError) as denied:
                            request("/api/v1/internal/faults", {}, cookie)
                        self.assertEqual(denied.exception.code, 404)
                        with self.assertRaises(HTTPError) as denied:
                            request(path, {}, cookie, origin="https://outside.example")
                        self.assertEqual(denied.exception.code, 403)
                        self.assertEqual(json.load(request("/api/v1/command-center/logout", {}, cookie))["connected"], False)
                        with self.assertRaises(HTTPError) as denied:
                            request(path, {}, cookie)
                        self.assertEqual(denied.exception.code, 401)
                        forwarded = [call for call in Stub.calls if call[0] == "POST" and call[1] != "/api/v1/auth/login"]
                        self.assertEqual(len(forwarded), 2)
                        self.assertTrue(all(auth == "Bearer engineer-token" for _, _, auth in forwarded))
                    finally:
                        for server in (preview, gateway): server.shutdown()
                        for thread in threads: thread.join(timeout=2)


if __name__ == "__main__": unittest.main()
