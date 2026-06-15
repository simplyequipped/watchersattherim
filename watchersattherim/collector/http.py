"""Read-only HTTP/REST query surface, stdlib only.

Uses the stdlib ``http.server`` to keep the collector dependency-free; a thin
adapter over ``commands.dispatch``. The route tail under ``/api/v1`` is the
command name (so ``/api/v1/trend/path/hour`` -> ``trend/path/hour``), with the
single exception of ``/api/v1/monitors/<address>`` -> ``monitor``. Observations
enter only via LXMF, so this surface is read-only.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .commands import (
    COMMANDS, INTERNAL_ERROR, INVALID_COMMAND, INVALID_PARAMS, CommandError, dispatch,
)
from ..propagation.config import PropagationConfig

API = "/api/v1"
_ROUTABLE = set(COMMANDS) - {"monitor"}
_MONITORS = "monitors/"


def route(path: str, params: dict) -> tuple[str, dict]:
    """Map an HTTP path + query params to a (command, params) pair."""
    prefix = f"{API}/"
    if not path.startswith(prefix):
        raise CommandError(INVALID_COMMAND, f"no such endpoint: {path}")
    rest = path[len(prefix):]
    if rest in _ROUTABLE:
        return rest, params
    if rest.startswith(_MONITORS) and rest[len(_MONITORS):]:
        return "monitor", {**params, "address": rest[len(_MONITORS):]}
    raise CommandError(INVALID_COMMAND, f"no such endpoint: {path}")


def make_handler(conn, lock: threading.Lock, stats, clock=time.time,
                 propagation: PropagationConfig = None):
    prop = propagation or PropagationConfig()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                command, cmd_params = route(parsed.path, params)
                with lock:
                    result = dispatch(conn, stats, command, cmd_params,
                                      now=int(clock()), propagation=prop)
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


def make_server(conn, lock, stats, *, bind="0.0.0.0", port=8080, clock=time.time,
                propagation: PropagationConfig = None):
    return ThreadingHTTPServer(
        (bind, port), make_handler(conn, lock, stats, clock, propagation))
