"""Monitor orchestration: drivers -> pipeline -> batcher/queue -> flush timer.

One receiver process (ft8mon or wsprmon) per receiver feeds decodes, dispatched
by mode to its pipeline, into a shared telemetry batcher; a timer flushes a batch
to the collector every ``send_interval``. The per-decode and flush paths are
guarded by a lock so the driver threads and the timer don't race.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

from .. import __version__
from .cache import CallsignCache
from .config import (Config, Receiver, list_audio_devices, resolve_card_desc,
                     sdrfanout_argv)
from .driver import ReceiverDriver
from .ft8_pipeline import ingest as ft8_ingest
from .wspr_pipeline import ingest as wspr_ingest
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
        self._drivers: list[ReceiverDriver] = []
        self._driver_by_name: dict[str, ReceiverDriver] = {}
        self._stop = threading.Event()
        self._window_start = int(self._clock())

        # No-decode watchdog: restart a receiver that is alive but silent. Each
        # receiver has its own threshold (seconds, 0 = disabled), checked per flush.
        self._silent_limit = {r.name: r.restart_after_silent_sec for r in config.receivers}
        self._window_decodes: dict[str, int] = {}
        self._silent_sec: dict[str, int] = {}

    # --- per-decode -------------------------------------------------------

    def handle_line(self, receiver: Receiver, line: str) -> None:
        now = self._clock()
        with self._lock:
            if receiver.mode == "wspr":
                result = wspr_ingest(line, receiver.freq, self.config.monitor.grid,
                                     min_snr=receiver.min_decode_snr,
                                     blacklist=self.config.blacklist,
                                     snr_ceiling=receiver.snr_ceiling)
            else:
                result = ft8_ingest(line, receiver.freq, self.config.monitor.grid,
                                    self._obs_cache, ts=now,
                                    min_snr=receiver.min_decode_snr,
                                    blacklist=self.config.blacklist,
                                    snr_ceiling=receiver.snr_ceiling)
            if result is None:
                return                       # not a decode line
            self.batcher.note_decode()
            # count the decode for the watchdog (a weak-but-filtered decode still
            # means the receiver is alive)
            self._window_decodes[receiver.name] = (
                self._window_decodes.get(receiver.name, 0) + 1
            )
            added = 0
            for obs, freq_hz in result:
                if obs.kind == "indirect" and not self.config.observations.indirect:
                    continue
                self.batcher.add(obs, ts=_slot(now), freq_hz=freq_hz,
                                 band=receiver.band, mode=receiver.mode.upper())
                added += 1
            # -v mirrors the collector by default (only decodes kept as observations).
            # debug echoes the full raw decode firehose. A mode column tells the
            # interleaved FT8/WSPR lines apart.
            if self.verbose and (self.config.monitor.debug or added > 0):
                print(f"{receiver.mode.upper():4} {line}", flush=True)

    # --- window flush -----------------------------------------------------

    def flush(self) -> bool:
        now = int(self._clock())
        with self._lock:
            ok = flush_window(
                batcher=self.batcher, queue=self.queue, sender=self.sender,
                monitor=self.meta, window_start=self._window_start,
                window_end=now, cache_size=len(self.cache),
                send_empty=self.config.collector.send_empty_batches,
            )
            self._window_start = now
        return ok

    def _check_watchdog(self) -> None:
        """Restart any receiver silent past its own restart_after_silent threshold.

        Runs once per flush, so each silent window adds ``send_interval`` seconds to
        a receiver's silence. A decode resets it.
        """
        interval = self.config.collector.send_interval
        with self._lock:
            decodes = dict(self._window_decodes)
            self._window_decodes.clear()
        for name, driver in self._driver_by_name.items():
            limit = self._silent_limit.get(name, 0)
            if limit <= 0:
                continue                     # watchdog disabled for this receiver
            if decodes.get(name, 0) > 0:
                self._silent_sec[name] = 0
                continue
            elapsed = self._silent_sec.get(name, 0) + interval
            self._silent_sec[name] = elapsed
            if elapsed >= limit:
                self._log(f"no decodes from receiver [{name}] in {elapsed}s")
                driver.bounce()
                self._silent_sec[name] = 0

    # --- lifecycle --------------------------------------------------------

    def _sdrfanout_driver(self) -> Optional[ReceiverDriver]:
        """Build (not start) the sdrfanout producer for the shared-SDR receivers.

        One sdrfanout process owns the radio and fans it out to a FIFO per
        ``sdr = yes`` receiver. We create the FIFOs here so a decoder's open()
        finds them (and blocks for the writer) regardless of start order. Returns
        None when no receiver feeds off the shared SDR.
        """
        streams = [r for r in self.config.receivers if r.kind == "sdr"]
        if not streams or self.config.sdr is None:
            return None
        sdr = self.config.sdr
        for r in streams:
            os.makedirs(os.path.dirname(r.path), exist_ok=True)
            try:
                os.mkfifo(r.path)
            except FileExistsError:
                pass
        return ReceiverDriver(
            sdrfanout_argv(sdr, streams),
            capture_stderr=True, label="sdrfanout", logger=self._log,
            log_command=self.config.monitor.debug,
        )

    def _resolve_cards(self) -> None:
        """Resolve any ``card = DESC`` receivers to a device number via the decoder's -list.

        A receiver carries an unresolved ``card_desc`` until here, where we run the
        decoder its mode uses (wsprmon for wspr, else ft8mon) with ``-list`` and match
        the description against the listed device names. The ``-list`` output is cached
        per binary so two receivers off the same decoder enumerate the devices once.
        """
        listed: dict[str, list[tuple[int, str]]] = {}
        for r in self.config.receivers:
            if r.kind != "audio" or r.card_desc is None:
                continue
            binary = (self.config.wsprmon_path if r.mode == "wspr"
                      else self.config.ft8mon_path)
            if binary not in listed:
                listed[binary] = list_audio_devices(binary)
            r.card = resolve_card_desc(f"receiver:{r.name}", r.card_desc, listed[binary])

    def run(self) -> None:
        self._resolve_cards()
        # Start the shared SDR first (if any), so its FIFOs exist and it is writing
        # before the decoders open them. Decoders are decoupled from its restarts:
        # StreamSoundIn resyncs on the wsf magic after a gap, and a long outage trips
        # the silent-decode watchdog.
        producer = self._sdrfanout_driver()
        if producer is not None:
            self._drivers.append(producer)
            threading.Thread(target=producer.run, name="sdrfanout", daemon=True).start()

        for receiver in self.config.receivers:
            if receiver.mode == "wspr":
                workdir = os.path.join(self.config.wsprmon_working_dir, receiver.name)
                os.makedirs(workdir, exist_ok=True)
                argv = [self.config.wsprmon_path,
                        *receiver.wsprmon_args(self.config.wsprd_path, workdir)]
            else:
                argv = [self.config.ft8mon_path, *receiver.ft8mon_args()]
            driver = ReceiverDriver(
                argv,
                on_line=lambda line, r=receiver: self.handle_line(r, line),
                label=f"{os.path.basename(argv[0])} [{receiver.name}]",
                logger=self._log,
                log_command=self.config.monitor.debug,
            )
            self._drivers.append(driver)
            self._driver_by_name[receiver.name] = driver
            threading.Thread(
                target=driver.run, name=f"{receiver.mode}-{receiver.name}", daemon=True
            ).start()

        while not self._stop.wait(self.config.collector.send_interval):
            self.flush()
            self._check_watchdog()
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
