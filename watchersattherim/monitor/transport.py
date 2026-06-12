"""Telemetry transport: sender interface, pending queue, window flush.

All RNS-free and unit-tested. The live LXMF sender lives in ``lxmf_setup``; here
we define the ``Sender`` protocol, a dry-run ``StdoutSender``, the bounded
pending queue, and the per-window flush.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Protocol

from .telemetry import TelemetryBatcher, build_batch


class Sender(Protocol):
    def send(self, batch: dict) -> bool:
        """Send one telemetry batch. Return True on success."""
        ...


class StdoutSender:
    """Dry-run sender: writes each batch as a JSON line to a stream."""

    def __init__(self, stream=None):
        self._stream = stream or sys.stdout

    def send(self, batch: dict) -> bool:
        print(json.dumps(batch), file=self._stream, flush=True)
        return True


@dataclass
class PendingQueue:
    """Bounded FIFO of observation rows held when a send fails.

    When capacity is exceeded the oldest rows are dropped and counted; the count
    surfaces in the next successful telemetry message's stats.
    """

    max_observations: int
    _items: list = field(default_factory=list)
    dropped: int = 0

    def __len__(self) -> int:
        return len(self._items)

    def add_many(self, rows: list) -> None:
        self._items.extend(rows)
        overflow = len(self._items) - self.max_observations
        if overflow > 0:
            del self._items[:overflow]
            self.dropped += overflow

    def drain(self) -> list:
        items, self._items = self._items, []
        return items


def flush_window(
    *,
    batcher: TelemetryBatcher,
    queue: PendingQueue,
    sender: Sender,
    monitor: dict,
    window_start: int,
    window_end: int,
    cache_size: int = 0,
) -> bool:
    """Build and send a batch of (pending + current) observations.

    On success the pending queue and drop counter are cleared. On failure the
    rows return to the pending queue (bounded). The decode counter resets either
    way, since it counts decodes seen in the just-closed window.
    """
    rows = queue.drain() + batcher.take()
    batch = build_batch(
        monitor, window_start, window_end, rows,
        decodes_seen=batcher.decodes_seen,
        obs_dropped_queue=queue.dropped,
        cache_size=cache_size,
    )
    ok = sender.send(batch)
    if ok:
        queue.dropped = 0
    else:
        queue.add_many(rows)
    batcher.reset_counters()
    return ok
