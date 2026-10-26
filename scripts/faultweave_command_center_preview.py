"""Serve the existing Site and a scoped local engineer gateway bridge.

Session actions forward only on explicit engineer requests; local bind only.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import time
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from http.client import HTTPConnection
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Callable

from scripts.faultweave_candidate04_readout import candidate_readout
from scripts.faultweave_command_center_cases import CaseRouteError, case_detail, list_cases


ROUTE = "/api/v1/command-center/snapshot"
CASES_ROUTE = "/api/v1/command-center/cases"
CASE_ID_ROUTE = re.compile(r"/api/v1/command-center/cases/([0-9a-fA-F-]{36})\Z")
LOGIN_ROUTE = "/api/v1/command-center/login"
SESSION_ROUTE = "/api/v1/command-center/session"
LOGOUT_ROUTE = "/api/v1/command-center/logout"
DEMO_SESSIONS = "/api/v1/demo/sessions"
DEMO_ID_ROUTE = re.compile(r"/api/v1/demo/sessions/(demo-[a-zA-Z0-9]+)\Z")
DEMO_ACTION_ROUTE = re.compile(r"/api/v1/demo/sessions/(demo-[a-zA-Z0-9]+)/(arm|execute|analyze|reveal|approve|recover|run)\Z")
MAX_BODY_BYTES = 262_144
MAX_AGE_SECONDS = 20


def _gateway_get(port: int, path: str, authorization: str = "") -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        headers = {"Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("Gateway response too large")
        return response.status, json.loads(body)
    finally:
        connection.close()


def _gateway_post(port: int, path: str, payload: dict, authorization: str = "") -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", port, timeout=420 if path.endswith("/run") else 30)
    try:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        connection.request("POST", path, body=json.dumps(payload).encode("utf-8"), headers=headers)
        response = connection.getresponse()
        body = response.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("Gateway response too large")
        return response.status, json.loads(body)
    finally:
        connection.close()


def _age(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Gateway timestamp lacks timezone")
    return (now - parsed.astimezone(UTC)).total_seconds()


def _incident(row: dict | None) -> dict | None:
    if row is None:
        return None
    classification = row.get("classification") or {}
    localization = row.get("localization") or {}
    return {
        "incident_id": row.get("incident_id"),
        "state": row.get("state"),
        "detected_at": row.get("detected_at"),
        "updated_at": row.get("updated_at"),
        "fault_family": classification.get("label"),
        "probable_origin": localization.get("probable_origin"),
        "localization_method": localization.get("method"),
    }


def _legacy_gateway_snapshot(port: int, authorization: str) -> tuple[int, dict]:
    """Adapt pre-patch read-only gateway routes; require its engineer role first."""
    auth_status, _ = _gateway_get(port, "/api/v1/demo/contract", authorization)
    if auth_status not in (200,):
        return 403 if auth_status == 403 else 401, {"detail": "Engineer gateway session required"}
    feature_status, window = _gateway_get(port, "/api/v1/intelligence/live/features/60", authorization)
    current_status, current_payload = _gateway_get(port, "/api/v1/intelligence/incidents/current", authorization)
    list_status, listing = _gateway_get(port, "/api/v1/intelligence/incidents", authorization)
    if feature_status not in (200, 409) or current_status != 200 or list_status != 200:
        raise ValueError("Gateway read-only routes unavailable")
    now = datetime.now(UTC)
    telemetry = {
        "status": "INSUFFICIENT", "last_event_at": None,
        "age_seconds": None, "window_seconds": 60,
        "event_count": None, "request_count": None, "coverage_seconds": None,
        "detail": "No complete independent 60-second gateway window is available.",
    }
    if feature_status == 200:
        event_at = window.get("window_ended_at")
        age = _age(event_at, now)
        telemetry["last_event_at"] = event_at
        telemetry["age_seconds"] = round(age, 3) if age is not None else None
        if age is None or age > MAX_AGE_SECONDS or age < -5:
            telemetry.update(status="STALE", detail="Gateway telemetry watermark is stale or ahead of local time.")
        elif (window.get("window_seconds") == 60
              and window.get("request_observation_source") == "client_observed"
              and window.get("request_count", 0) > 0
              and window.get("event_count", 0) > 0
              and window.get("coverage_seconds", 0) >= 60):
            telemetry.update(
                status="READY", detail="Fresh independent gateway telemetry; this view performs no inference.",
                event_count=window["event_count"], request_count=window["request_count"],
                coverage_seconds=window["coverage_seconds"],
            )
    active = current_payload.get("incident") if current_payload.get("active") else None
    incident_age = _age(active.get("updated_at"), now) if active else None
    incident_current = active is None or (incident_age is not None and -5 <= incident_age <= MAX_AGE_SECONDS)
    rows = listing.get("incidents")
    if not isinstance(rows, list):
        raise ValueError("Invalid incident listing")
    return 200, {
        "schema_version": 1, "generated_at": now.isoformat().replace("+00:00", "Z"),
        "telemetry": telemetry,
        "incident_feed": {
            "source_model": "earlier_gateway_frozen_model",
            "status": "CURRENT" if telemetry["status"] == "READY" and incident_current else "UNVERIFIED",
            "count": len(rows), "current": _incident(active),
            "recent": [_incident(item) for item in rows[:5]],
            "durability": "in_memory_gateway_process",
        },
        "candidate04": {
            "status": "NOT_CONNECTED", "reason": "Candidate 04 remains a separate research shadow service.",
        },
    }


def make_handler(dist: Path, gateway_port: int, *, v2_gateway_port: int = 28110,
                 shadow_port: int = 28190, receipts: Path | None = None,
                 candidate_reader: Callable[..., dict] = candidate_readout) -> type[SimpleHTTPRequestHandler]:
    if not (dist / "index.html").is_file() or not (dist / "faultweave-live.js").is_file():
        raise ValueError("The existing Site preview needs dist/index.html and dist/faultweave-live.js")
    if any(port < 1 or port > 65535 for port in (gateway_port, v2_gateway_port, shadow_port)):
        raise ValueError("Invalid local port")

    sessions: dict[str, tuple[str, float]] = {}
    session_lock = Lock()

    class PreviewHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)

        def _authorization(self) -> str:
            explicit = self.headers.get("Authorization", "")
            if explicit.startswith("Bearer ") and len(explicit) <= 4096:
                return explicit
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                sid = cookie["faultweave_preview"].value
            except (KeyError, ValueError):
                return ""
            with session_lock:
                record = sessions.get(sid)
                if not record:
                    return ""
                if record[1] <= time.monotonic():
                    sessions.pop(sid, None)
                    return ""
                return "Bearer " + record[0]

        def _session_id(self) -> str | None:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                return cookie["faultweave_preview"].value
            except (KeyError, ValueError):
                return None

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin", "")
            host = self.headers.get("Host", "")
            expected = f"127.0.0.1:{self.server.server_port}"
            return host == expected and (not origin or origin == "http://" + expected)

        def _body(self) -> dict:
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or int(length) > 4096 or int(length) < 2:
                raise ValueError("Invalid request body length")
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                raise ValueError("Expected JSON")
            payload = json.loads(self.rfile.read(int(length)))
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object")
            return payload

        def do_POST(self) -> None:
            if not self._same_origin():
                self._json(403, {"detail": "Same-origin action required"})
                return
            if self.path not in (LOGIN_ROUTE, LOGOUT_ROUTE, DEMO_SESSIONS) and not DEMO_ACTION_ROUTE.fullmatch(self.path):
                self._json(404, {"detail": "Route not found"})
                return
            if self.path == LOGOUT_ROUTE:
                sid = self._session_id()
                if sid:
                    with session_lock:
                        sessions.pop(sid, None)
                self._json(200, {"connected": False}, cookie="faultweave_preview=; Path=/api/v1/; Max-Age=0; HttpOnly; SameSite=Strict")
                return
            try:
                body = self._body()
                if self.path == LOGIN_ROUTE:
                    if set(body) != {"username", "password"} or any(not isinstance(body[key], str) for key in body):
                        raise ValueError("Username and password required")
                    status, answer = _gateway_post(gateway_port, "/api/v1/auth/login", body)
                    token = answer.get("access_token")
                    if status != 200 or answer.get("role") != "engineer" or not isinstance(token, str) or len(token) > 4000:
                        self._json(403, {"detail": "Engineer login was rejected"})
                        return
                    auth_status, _ = _gateway_get(gateway_port, "/api/v1/demo/contract", "Bearer " + token)
                    if auth_status != 200:
                        self._json(403, {"detail": "Engineer access was not verified"})
                        return
                    ttl = max(1, min(int(answer.get("expires_in", 900)), 900))
                    sid = secrets.token_urlsafe(32)
                    with session_lock:
                        sessions[sid] = (token, time.monotonic() + ttl)
                    self._json(200, {"connected": True, "role": "engineer", "expires_in": ttl},
                               cookie=f"faultweave_preview={sid}; Path=/api/v1/; Max-Age={ttl}; HttpOnly; SameSite=Strict")
                    return
                authorization = self._authorization()
                if not authorization:
                    self._json(401, {"detail": "Engineer session required"})
                    return
                auth_status, _ = _gateway_get(gateway_port, "/api/v1/demo/contract", authorization)
                if auth_status != 200:
                    self._json(403, {"detail": "Engineer access required"})
                    return
                status, answer = _gateway_post(gateway_port, self.path, body, authorization)
                self._json(status, answer)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                self._json(503, {"detail": "Local gateway action is unavailable"})

        def do_GET(self) -> None:
            if self.path.startswith("/api/") and not self._same_origin():
                self._json(403, {"detail": "Local origin required"})
                return
            if self.path == SESSION_ROUTE:
                authorization = self._authorization()
                if authorization:
                    try:
                        status, _ = _gateway_get(gateway_port, "/api/v1/demo/contract", authorization)
                        if status == 200:
                            self._json(200, {"connected": True, "role": "engineer"})
                            return
                    except (OSError, ValueError):
                        pass
                self._json(401, {"connected": False})
                return
            if self.path == "/api/v1/demo/scenarios" or self.path == DEMO_SESSIONS + "?limit=25" or DEMO_ID_ROUTE.fullmatch(self.path):
                authorization = self._authorization()
                if not authorization:
                    self._json(401, {"detail": "Engineer session required"})
                    return
                try:
                    status, answer = _gateway_get(gateway_port, self.path, authorization)
                    self._json(status, answer)
                except (OSError, ValueError, json.JSONDecodeError):
                    self._json(503, {"detail": "Gateway demo history unavailable"})
                return
            if self.path == CASES_ROUTE or self.path.startswith(CASES_ROUTE + "/"):
                authorization = self._authorization()
                if not authorization.startswith("Bearer ") or len(authorization) > 4096:
                    self._json(401, {"detail": "Engineer bearer token required"})
                    return
                try:
                    if self.path == CASES_ROUTE:
                        result = list_cases(_gateway_get, gateway_port, authorization)
                    else:
                        match = CASE_ID_ROUTE.fullmatch(self.path)
                        if match is None:
                            self._json(404, {"detail": "Case evidence was not found"})
                            return
                        result = case_detail(_gateway_get, gateway_port, authorization, match.group(1))
                    self._json(200, result)
                except CaseRouteError as exc:
                    self._json(exc.status, {"detail": str(exc)})
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    self._json(503, {"detail": "Case evidence could not be verified"})
                return
            if self.path.split("?", 1)[0] != ROUTE:
                super().do_GET()
                return

            authorization = self._authorization()
            if not authorization.startswith("Bearer ") or len(authorization) > 4096:
                self._json(401, {"detail": "Engineer bearer token required"})
                return

            try:
                status, payload = _gateway_get(gateway_port, ROUTE, authorization)
                if status == 404:
                    status, payload = _legacy_gateway_snapshot(gateway_port, authorization)
                if status not in (200, 401, 403, 422):
                    raise ValueError("Gateway route unavailable")
                if status == 200:
                    if payload.get("schema_version") != 1:
                        raise ValueError("Unexpected gateway schema")
                    # Only after the primary gateway accepted the engineer session.
                    # The V2 readout itself never posts telemetry or calls inference.
                    payload["candidate04"] = candidate_reader(
                        lambda port, path: _gateway_get(port, path),
                        gateway_port=v2_gateway_port, shadow_port=shadow_port, receipts=receipts,
                    )
                else:
                    payload = {"detail": "Gateway rejected the engineer session"}
                self._json(status, payload)
            except (OSError, ValueError, json.JSONDecodeError):
                self._json(503, {"detail": "Local gateway snapshot is unavailable"})

        def _json(self, status: int, payload: dict, *, cookie: str | None = None) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                # A navigation can cancel an outstanding poll after headers were sent.
                pass

    return PreviewHandler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dist", required=True, type=Path)
    parser.add_argument("--gateway-port", type=int, default=18110)
    parser.add_argument("--v2-gateway-port", type=int, default=28110)
    parser.add_argument("--shadow-port", type=int, default=28190)
    parser.add_argument("--port", type=int, default=0, help="Local preview port (0 chooses a free port)")
    args = parser.parse_args()
    if args.port < 0 or args.port > 65535:
        parser.error("Invalid preview port")
    handler = make_handler(args.site_dist.resolve(), args.gateway_port,
                           v2_gateway_port=args.v2_gateway_port, shadow_port=args.shadow_port)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Open http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
