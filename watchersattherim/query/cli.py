"""LXMF query client for the collector.

Sends a query envelope to a collector's LXMF address and prints the reply. The
live send/receive path needs a running Reticulum instance and is not exercised by
the test suite; the param/envelope helpers are.

Protocol matches the collector listener: the query rides in FIELD_CUSTOM_DATA
namespaced by FIELD_CUSTOM_TYPE = APP_QUERY, and the reply comes back as APP_REPLY.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Optional

from ..common import lxmf
from ..common.protocol import APP_QUERY, APP_REPLY

DEFAULT_IDENTITY = "~/.watchersattherim/query/identity"


def parse_params(items: list[str]) -> dict:
    """Parse ``key=value`` CLI arguments into a params dict."""
    params: dict = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"parameter must be key=value: {item!r}")
        key, value = item.split("=", 1)
        params[key] = value
    return params


def build_query(cmd: str, params: dict, request_id: str) -> dict:
    return {"v": 1, "cmd": cmd, "params": params, "request_id": request_id}


class LxmfQueryClient:
    def __init__(self, router, source):
        self.router = router
        self.source = source
        self._replies: dict = {}
        self._event = threading.Event()
        router.register_delivery_callback(self._on_reply)

    def _on_reply(self, message) -> None:
        import LXMF

        fields = getattr(message, "fields", None) or {}
        if fields.get(LXMF.FIELD_CUSTOM_TYPE) == APP_REPLY:
            data = fields.get(LXMF.FIELD_CUSTOM_DATA)
            if isinstance(data, dict):
                self._replies[data.get("request_id")] = data
                self._event.set()

    def query(self, collector: bytes, cmd: str, params: dict, *, timeout: float = 30) -> dict:
        import LXMF

        deadline = time.time() + timeout
        dest = lxmf.resolve(collector)
        while dest is None and time.time() < deadline:
            time.sleep(0.5)
            dest = lxmf.resolve(collector)
        if dest is None:
            raise TimeoutError("no path to collector")

        request_id = os.urandom(8).hex()
        fields = {
            LXMF.FIELD_CUSTOM_TYPE: APP_QUERY,
            LXMF.FIELD_CUSTOM_DATA: build_query(cmd, params, request_id),
        }
        lxmf.send(self.router, self.source, dest, fields=fields)

        while time.time() < deadline:
            if request_id in self._replies:
                return self._replies[request_id]
            self._event.wait(0.5)
            self._event.clear()
        raise TimeoutError("no reply from collector")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watr-query",
        description="Query a watchersattherim collector over LXMF",
    )
    parser.add_argument("collector", help="collector LXMF address (hex)")
    parser.add_argument("command", help="query command (path, from, to, band, monitors, "
                                        "monitor, stats, channel, channel/anomaly, "
                                        "trend/path/hour, trend/band/hour, map, coverage)")
    parser.add_argument("params", nargs="*", help="key=value query parameters")
    parser.add_argument("--config-dir", help="Reticulum config directory")
    parser.add_argument("--identity", default=DEFAULT_IDENTITY,
                        help="path to this client's LXMF identity")
    parser.add_argument("--timeout", type=float, default=30, help="reply timeout (seconds)")
    args = parser.parse_args(argv)

    try:
        collector = bytes.fromhex(args.collector)
    except ValueError:
        print(f"invalid collector address: {args.collector}", file=sys.stderr)
        return 2
    try:
        params = parse_params(args.params)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    router, source = lxmf.setup(
        identity_path=args.identity,
        storage_dir=os.path.dirname(os.path.expanduser(args.identity)),
        display_name="watchersattherim query",
        reticulum_config_dir=args.config_dir,
    )
    client = LxmfQueryClient(router, source)

    try:
        reply = client.query(collector, args.command, params, timeout=args.timeout)
    except TimeoutError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(json.dumps(reply, indent=2))
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
