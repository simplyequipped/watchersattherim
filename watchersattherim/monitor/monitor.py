"""Monitor orchestration: drivers -> pipeline -> batcher/queue -> flush timer.

One ft8mon process per receiver feeds decodes into a shared cache and telemetry
batcher; a timer flushes a batch to the collector every ``send_interval``. The
per-decode and flush paths are guarded by a lock so the driver threads and the
timer don't race.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .. import __version__
from .cache import CallsignCache
from .config import Config, Receiver
from .driver import Ft8monDriver
from .parser import classify, parse_line
from .pipeline import process_decode
from .telemetry import TelemetryBatcher, monitor_meta
from .transport import PendingQueue, Sender, flush_window

SLOT_SECONDS = 15  # FT8 transmit slot


class _NullCache:
    """Stand-in when the callsign cache is disabled: never learns or backfills."""

    def update(self, *args, **kwargs) -> None:
        pass

    def lookup(self, *args, **kwargs) -> None:
        return None

    def __len__(self) -> int:
        return 0


def _slot(now: float) -> int:
    return (int(now) // SLOT_SECONDS) * SLOT_SECONDS


class Monitor:
    def __init__(self, config: Config, sender: Sender, *,
                 clock: Callable[[], float] = time.time,
                 logger: Optional[Callable[[str], None]] = None,
                 verbose: bool = False):
        self.config = config
        self.sender = sender
        self._clock = clock
        self._log = logger or (lambda _m: None)
        self.verbose = verbose

        self.cache = CallsignCache(
            max_entries=config.cache.max_entries,
            entry_ttl_sec=config.cache.ttl_sec,
            clock=clock,
        )
        if config.cache.enabled and config.cache.persist:
            try:
                self.cache.load(config.storage.cache_path)
            except Exception as e:  # noqa: BLE001
                self._log(f"cache load failed: {e}")
        self._obs_cache = self.cache if config.cache.enabled else _NullCache()

        self.batcher = TelemetryBatcher(include_callsigns=config.observations.callsigns)
        self.queue = PendingQueue(config.collector.max_pending_observations)
        self.meta = monitor_meta(
            config.monitor.grid,
            f"watchersattherim-{__version__}",
            lat=config.monitor.lat,
            lon=config.monitor.lon,
        )

        self._lock = threading.Lock()
        self._drivers: list[Ft8monDriver] = []
        self._stop = threading.Event()
        self._window_start = int(self._clock())

    # --- per-decode -------------------------------------------------------

    def handle_line(self, receiver: Receiver, line: str) -> None:
        decode = parse_line(line)
        if decode is None:
            return
        if self.verbose:
            print(line, flush=True)
        now = self._clock()
        with self._lock:
            self.batcher.note_decode()
            observations = process_decode(
                decode, classify(decode.message),
                self.config.monitor.grid, self._obs_cache, ts=now,
            )
            freq_hz = receiver.freq + int(round(decode.freq))
            for obs in observations:
                if obs.kind == "indirect" and not self.config.observations.indirect:
                    continue
                self.batcher.add(obs, ts=_slot(now), freq_hz=freq_hz, band=receiver.band)

    # --- window flush -----------------------------------------------------

    def flush(self) -> bool:
        now = int(self._clock())
        with self._lock:
            ok = flush_window(
                batcher=self.batcher, queue=self.queue, sender=self.sender,
                monitor=self.meta, window_start=self._window_start,
                window_end=now, cache_size=len(self.cache),
            )
            self._window_start = now
        return ok

    # --- lifecycle --------------------------------------------------------

    def run(self) -> None:
        for receiver in self.config.receivers:
            argv = [self.config.ft8mon_path, *receiver.ft8mon_args()]
            driver = Ft8monDriver(
                argv,
                on_line=lambda line, r=receiver: self.handle_line(r, line),
                logger=self._log,
            )
            self._drivers.append(driver)
            threading.Thread(
                target=driver.run, name=f"ft8mon-{receiver.band}", daemon=True
            ).start()
            self._log(f"started ft8mon [{receiver.band}]: {' '.join(argv)}")

        while not self._stop.wait(self.config.collector.send_interval):
            self.flush()
        self.flush()
        self._persist()

    def stop(self) -> None:
        self._stop.set()
        for driver in self._drivers:
            driver.stop()

    def _persist(self) -> None:
        if self.config.cache.enabled and self.config.cache.persist:
            try:
                self.cache.save(self.config.storage.cache_path)
            except Exception as e:  # noqa: BLE001
                self._log(f"cache persist failed: {e}")
