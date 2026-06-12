"""Read-only HTTP/REST query surface, stdlib only.

Uses the stdlib ``http.server`` to keep the collector dependency-free; a thin
adapter over ``commands.dispatch``, trivially swappable. Observations enter only
via LXMF, so this surface is read-only.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .commands import (
    INTERNAL_ERROR, INVALID_COMMAND, INVALID_PARAMS, CommandError, dispatch,
)

API = "/api/v1"

_ROUTES = {
    f"{API}/path": "path_query",
    f"{API}/from": "from_grid",
    f"{API}/to": "to_grid",
    f"{API}/band": "band_activity",
    f"{API}/monitors": "monitor_list",
    f"{API}/stats": "stats",
}


def route(path: str, params: dict) -> tuple[str, dict]:
    """Map an HTTP path + query params to a (command, params) pair."""
    if path in _ROUTES:
        return _ROUTES[path], params
    prefix = f"{API}/monitors/"
    if path.startswith(prefix) and path[len(prefix):]:
        return "monitor_info", {**params, "address": path[len(prefix):]}
    raise CommandError(INVALID_COMMAND, f"no such endpoint: {path}")


def make_handler(conn, lock: threading.Lock, stats, clock=time.time):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                command, cmd_params = route(parsed.path, params)
                with lock:
                    result = dispatch(conn, stats, command, cmd_params, now=int(clock()))
                self._reply(200, {"ok": True, "result": result})
            except CommandError as e:
                code = 400 if e.code in (INVALID_COMMAND, INVALID_PARAMS) else 500
                self._reply(code, {"ok": False, "error_code": e.code, "error": str(e)})
            except Exception:
                self._reply(500, {"ok": False, "error_code": INTERNAL_ERROR,
                                  "error": "internal error"})

        def _reply(self, code: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    return Handler


def make_server(conn, lock, stats, *, bind="0.0.0.0", port=8080, clock=time.time):
    return ThreadingHTTPServer((bind, port), make_handler(conn, lock, stats, clock))
