"""Live LXMF inbound listener for the collector.

Untested in CI (needs a running Reticulum instance). Handles two inbound message
types, distinguished by FIELD_CUSTOM_TYPE:

- telemetry  (``APP_OBS``)   -> validate + ingest the observation batch
- query      (``APP_QUERY``) -> dispatch and reply with ``APP_REPLY``

Each payload rides in FIELD_CUSTOM_DATA as gzipped msgpack (``common.wire``), the
same codec the monitor and query client use, so the nodes interoperate.
"""

from __future__ import annotations

import threading
from typing import Callable

from . import storage
from ..common import lxmf
from ..common.protocol import APP_OBS, APP_QUERY, APP_REPLY
from ..common.wire import decode, encode
from .admin import handle_admin_command
from .commands import INTERNAL_ERROR, NOT_AUTHORIZED, CommandError, dispatch
from .ingest import ingest_batch


class CollectorListener:
    def __init__(self, router, source, conn, lock: threading.Lock, stats, config,
                 clock: Callable[[], float], log: Callable[[str], None] = lambda _m: None):
        self.router = router
        self.source = source
        self.conn = conn
        self.lock = lock
        self.stats = stats
        self.config = config
        self.clock = clock
        self.log = log
        self._admins = {h.lower() for h in (config.admin_allowed or ())}
        router.register_delivery_callback(self.on_inbound)

    def on_inbound(self, message) -> None:
        try:
            import LXMF

            fields = getattr(message, "fields", None) or {}
            ftype = fields.get(LXMF.FIELD_CUSTOM_TYPE)
            data = fields.get(LXMF.FIELD_CUSTOM_DATA)
            source = message.source_hash
            if ftype == APP_OBS and isinstance(data, (bytes, bytearray)):
                self._ingest(source, decode(data))
            elif ftype == APP_QUERY and isinstance(data, (bytes, bytearray)):
                self._handle_query(source, decode(data))
            elif self._is_admin(source):
                self._handle_admin(source, message)
        except Exception as e:  # noqa: BLE001
            self.log(f"inbound error: {e}")

    def _is_admin(self, source: bytes) -> bool:
        return source.hex() in self._admins

    def _handle_admin(self, source: bytes, message) -> None:
        text = message.content_as_string() or ""
        with self.lock:
            reply = handle_admin_command(self.conn, self.stats, text, now=int(self.clock()))
        self.log(f"admin {source.hex()[:12]}: {text.strip().split(' ')[0] if text.strip() else 'help'}")
        self._reply_text(source, reply)

    def _reply_text(self, source: bytes, text: str) -> None:
        dest = lxmf.resolve(source)
        if dest is not None:
            lxmf.send(self.router, self.source, dest, content=text)

    def _ingest(self, source: bytes, batch: dict) -> None:
        now = int(self.clock())
        with self.lock:
            result = ingest_batch(self.conn, source, batch, config=self.config, now=now)
            self.stats.record_ingest(result)
        rejected = (result.rejected_allowlist + result.rejected_timestamp
                    + result.rejected_schema)
        self.log(f"ingest {source.hex()[:12]}: +{result.accepted} "
                 f"dup={result.duplicates} rejected={rejected}")

    def _handle_query(self, source: bytes, envelope: dict) -> None:
        request_id = envelope.get("request_id")
        now = int(self.clock())
        with self.lock:
            self.stats.record_query(source.hex())
            blocked = storage.is_query_blocked(self.conn, source)
        if blocked:
            self._reply(source, {"v": 1, "request_id": request_id, "ok": False,
                                 "error_code": NOT_AUTHORIZED, "error": "query access denied"})
            return
        try:
            with self.lock:
                result = dispatch(
                    self.conn, self.stats,
                    envelope.get("cmd"), envelope.get("params") or {}, now=now,
                    propagation=getattr(self.config, "propagation", None),
                )
            reply = {"v": 1, "request_id": request_id, "ok": True, "result": result}
        except CommandError as e:
            reply = {"v": 1, "request_id": request_id, "ok": False,
                     "error_code": e.code, "error": str(e)}
        except Exception:
            reply = {"v": 1, "request_id": request_id, "ok": False,
                     "error_code": INTERNAL_ERROR, "error": "internal error"}
        self._reply(source, reply)

    def _reply(self, source: bytes, envelope: dict) -> None:
        import LXMF

        dest = lxmf.resolve(source)
        if dest is None:
            return
        fields = {LXMF.FIELD_CUSTOM_TYPE: APP_REPLY, LXMF.FIELD_CUSTOM_DATA: encode(envelope)}
        lxmf.send(self.router, self.source, dest, fields=fields)
