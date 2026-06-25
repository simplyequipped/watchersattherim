"""Load and validate the monitor's INI configuration.

Only ``[monitor] grid``, at least one enabled ``[receiver:*]``, and
``[collector] address`` are required; everything else has a default. Each
``[receiver:NAME]`` section is one receiver process: NAME is its unique id,
``band`` defaults to NAME, ``mode`` is ``ft8`` (default) or ``wspr``, and
``enabled = false`` keeps a section configured but not running. Its source is
exactly one of ``card`` (audio device), ``path`` (WAV file), ``input`` (a
directly-attached SDR backend the decoder opens itself), or ``sdr = yes`` (a
channel off the shared SDR configured in ``[sdr]``, fanned out by sdrfanout).
"""

from __future__ import annotations

import configparser
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

from ..common.config import ConfigError, parse_duration, parser
from .observations import grid_to_latlon


# --- receivers ------------------------------------------------------------

SDR_INPUTS = ("airspy", "sdrip", "hpsdr", "cloudsdr")
SDR_NEEDS_IP = ("sdrip", "hpsdr", "cloudsdr")


def _mhz(freq_hz: int) -> str:
    return ("%.6f" % (freq_hz / 1_000_000)).rstrip("0").rstrip(".")


@dataclass
class Receiver:
    name: str                       # unique receiver id (the section name)
    band: str
    freq: int
    # "audio" (card) | "file" (path) | "backend" (input=, a native SDR the decoder
    # opens directly) | "sdr" (sdr=yes, a channel off the shared [sdr] via sdrfanout).
    # NB: kind "sdr" comes from the config key `sdr`, NOT from `input` (that's "backend").
    kind: str
    mode: str = "ft8"               # "ft8" | "wspr"
    card: Optional[int] = None
    channel: int = 0
    path: Optional[str] = None      # WAV path (file), or the FIFO path (sdr)
    input: Optional[str] = None
    serial: Optional[str] = None
    ip: Optional[str] = None
    args: list = field(default_factory=list)
    min_decode_snr: int = -25            # drop this receiver's decodes below this SNR
    restart_after_silent_sec: int = 0    # 0 = no-decode watchdog disabled for this receiver

    def _source_args(self) -> list[str]:
        """The ``-card …`` input selector shared by ft8mon and wsprmon."""
        if self.kind == "audio":
            return ["-card", str(self.card), str(self.channel)]
        if self.kind == "file":
            return ["-card", "file", self.path]
        if self.kind == "sdr":
            # a wsf stream from sdrfanout. The decoders' word for it is "stream".
            return ["-card", "stream", self.path]
        # backend: ft8mon/wsprmon native SDR (airspy/sdrip/hpsdr/cloudsdr)
        ident = (self.serial or "") if self.input == "airspy" else self.ip
        return ["-card", self.input, f"{ident},{_mhz(self.freq)}"]

    def ft8mon_args(self) -> list[str]:
        """Arguments this receiver launches ft8mon with."""
        return [*self._source_args(), *self.args]

    def wsprmon_args(self, wsprd_path: Optional[str] = None,
                     workdir: Optional[str] = None) -> list[str]:
        """Arguments this receiver launches wsprmon with.

        We run wsprmon in offset mode, ``-hz`` and no ``-f``, so it reports the
        audio offset in Hz and the monitor adds the dial, the same as FT8. It reads
        a file via ``-file`` (last, as it consumes the rest), and audio or SDR reuse
        the shared ``-card`` selector. ``workdir`` (``-a``) is where wsprmon writes
        its per-cycle wav, each receiver gets its own so they never collide.
        """
        args: list[str] = []
        if wsprd_path:
            args += ["-wsprd", wsprd_path]
        if workdir:
            args += ["-a", workdir]
        args += ["-hz"]
        if self.kind == "file":
            args += [*self.args, "-file", self.path]
        else:
            args += [*self._source_args(), *self.args]
        return args


# --- shared SDR (sdrfanout) -----------------------------------------------

@dataclass
class Sdr:
    """One physical SDR, fanned out to its ``sdr = yes`` receivers by sdrfanout.

    Device settings apply to the whole radio (all channels). Only a receiver's
    dial (``freq``) and FIFO are per channel. ``runtime_dir`` is where the monitor
    creates the channel FIFOs.
    """
    runtime_dir: str                 # resolved and expanded, <storage.dir>/run by default
    driver: str = ""                 # device name, e.g. "hackrf". "" = first device
    gain: Optional[str] = None       # dB string, or None/"auto" = device default
    rate: Optional[int] = None       # Hz. None = auto (smallest int x12k spanning channels)
    center: Optional[int] = None     # Hz. None = auto (lowest dial minus guard)
    guard: int = 10000               # Hz between LO and the lowest channel
    ppm: float = 0.0                 # frequency correction (no-op on some devices)
    antenna: Optional[str] = None    # Soapy antenna name. None = device default
    buffer: float = 1.0              # per-channel output buffer (sec)
    path: str = "sdrfanout"          # the sdrfanout binary


def sdrfanout_argv(sdr: Sdr, receivers: list["Receiver"]) -> list[str]:
    """Build the sdrfanout command line for the given shared-SDR receivers.

    Device flags come from ``sdr``. Each receiver contributes ``-ch <freq>:<fifo>``.
    Auto fields (gain/rate/center) are omitted so sdrfanout applies its own
    defaults (notably it picks the integer-x12k rate spanning all the channels).
    """
    argv = [sdr.path]
    if sdr.driver:
        argv += ["-driver", sdr.driver]   # device name, sdrfanout makes it driver=<name>
    if sdr.gain and sdr.gain != "auto":
        argv += ["-gain", sdr.gain]
    if sdr.rate:
        argv += ["-rate", str(sdr.rate)]
    if sdr.center:
        argv += ["-center", str(sdr.center)]
    argv += ["-guard", str(sdr.guard)]
    if sdr.ppm:
        argv += ["-ppm", str(sdr.ppm)]
    if sdr.antenna:
        argv += ["-antenna", sdr.antenna]
    argv += ["-buffer", str(sdr.buffer)]
    for r in receivers:
        argv += ["-ch", f"{r.freq}:{r.path}"]
    return argv


# --- config sections ------------------------------------------------------

@dataclass
class Monitor:
    grid: str
    lat: float
    lon: float
    debug: bool = False   # -v echoes every raw decode (else only ones kept as observations)


@dataclass
class Observations:
    callsigns: bool = False
    indirect: bool = True


@dataclass
class Cache:
    enabled: bool = True
    max_entries: int = 10000
    ttl_sec: int = 7200
    persist: bool = False


@dataclass
class Collector:
    address: str
    send_interval: int = 120   # seconds between batches (>= the 120 s WSPR slot)
    delivery: str = "direct"
    propagation_node: Optional[str] = None
    max_pending_observations: int = 50000
    send_empty_batches: bool = False


@dataclass
class Storage:
    dir: str = "~/.watchersattherim"

    @property
    def identity_path(self) -> str:
        return os.path.join(os.path.expanduser(self.dir), "identity")

    @property
    def cache_path(self) -> str:
        return os.path.join(os.path.expanduser(self.dir), "cache.db")


@dataclass
class Reticulum:
    config_dir: Optional[str] = None


@dataclass
class Config:
    monitor: Monitor
    receivers: list[Receiver]
    collector: Collector
    ft8mon_path: str = "ft8mon"
    wsprmon_path: str = "wsprmon"
    wsprd_path: Optional[str] = None        # None: wsprmon self-resolves wsprd
    sdr: Optional[Sdr] = None               # the shared SDR. None when no [sdr] section
    observations: Observations = field(default_factory=Observations)
    cache: Cache = field(default_factory=Cache)
    storage: Storage = field(default_factory=Storage)
    reticulum: Reticulum = field(default_factory=Reticulum)


# --- loading --------------------------------------------------------------

def load(path: str) -> Config:
    cp = parser()
    if not cp.read(os.path.expanduser(path)):
        raise ConfigError(f"config file not found: {path}")
    return from_parser(cp)


def loads(text: str) -> Config:
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#",))
    cp.read_string(text)
    return from_parser(cp)


def from_parser(cp: configparser.ConfigParser) -> Config:
    monitor = _monitor(cp)
    # storage + [sdr] first: a `sdr = yes` receiver's FIFO lives under the SDR's
    # runtime_dir, which defaults under storage.dir, so both must be known to
    # resolve the receiver's path.
    storage = Storage(dir=cp.get("storage", "dir", fallback="~/.watchersattherim"))
    sdr = _sdr(cp, storage)
    receivers = _receivers(cp, sdr)
    collector = _collector(cp)

    obs = Observations(
        callsigns=cp.getboolean("observations", "callsigns", fallback=False),
        indirect=cp.getboolean("observations", "indirect", fallback=True),
    )
    cache = Cache(
        enabled=cp.getboolean("cache", "enabled", fallback=True),
        max_entries=cp.getint("cache", "max_entries", fallback=10000),
        ttl_sec=parse_duration(cp.get("cache", "ttl", fallback="2h")),
        persist=cp.getboolean("cache", "persist", fallback=False),
    )
    reticulum = Reticulum(config_dir=cp.get("reticulum", "config_dir", fallback=None))

    wsprd_path = cp.get("wsprmon", "wsprd_path", fallback=None)
    return Config(
        monitor=monitor,
        receivers=receivers,
        collector=collector,
        sdr=sdr,
        ft8mon_path=os.path.expanduser(cp.get("ft8mon", "path", fallback="ft8mon")),
        wsprmon_path=os.path.expanduser(cp.get("wsprmon", "path", fallback="wsprmon")),
        wsprd_path=os.path.expanduser(wsprd_path) if wsprd_path else None,
        observations=obs,
        cache=cache,
        storage=storage,
        reticulum=reticulum,
    )


def _monitor(cp: configparser.ConfigParser) -> Monitor:
    grid = cp.get("monitor", "grid", fallback=None)
    if not grid:
        raise ConfigError("[monitor] grid is required")
    lat = cp.getfloat("monitor", "lat", fallback=None)
    lon = cp.getfloat("monitor", "lon", fallback=None)
    if lat is None or lon is None:
        try:
            glat, glon = grid_to_latlon(grid)
        except ValueError as e:
            raise ConfigError(str(e)) from e
        lat = glat if lat is None else lat
        lon = glon if lon is None else lon
    debug = cp.getboolean("monitor", "debug", fallback=False)
    return Monitor(grid=grid, lat=lat, lon=lon, debug=debug)


def _receivers(cp: configparser.ConfigParser,
               sdr: Optional[Sdr]) -> list[Receiver]:
    receivers: list[Receiver] = []
    any_section = False
    for section in cp.sections():
        if not section.startswith("receiver:"):
            continue
        any_section = True
        name = section.split(":", 1)[1].strip()
        if not name:
            raise ConfigError(f"[{section}] has no name")
        if not cp.getboolean(section, "enabled", fallback=True):
            continue
        receivers.append(_receiver(cp, section, name, sdr))
    if not any_section:
        raise ConfigError("at least one [receiver:NAME] section is required")
    if not receivers:
        raise ConfigError("no enabled [receiver:NAME] sections (all enabled=false)")
    return receivers


def _receiver(cp, section: str, name: str, sdr: Optional[Sdr]) -> Receiver:
    freq = cp.getint(section, "freq", fallback=None)
    if freq is None:
        raise ConfigError(f"[{section}] freq is required")
    band = cp.get(section, "band", fallback=name)
    mode = cp.get(section, "mode", fallback="ft8").lower()
    if mode not in ("ft8", "wspr"):
        raise ConfigError(f"[{section}] mode must be ft8 or wspr, got {mode!r}")
    card = cp.get(section, "card", fallback=None)
    path = cp.get(section, "path", fallback=None)
    inp = cp.get(section, "input", fallback=None)
    args = shlex.split(cp.get(section, "args", fallback=""))

    # drop weak decodes: default just below each mode's floor (FT8 ~-24, WSPR ~-28)
    min_snr = cp.getint(section, "min_decode_snr",
                        fallback=-25 if mode == "ft8" else -30)
    ras = cp.get(section, "restart_after_silent", fallback=None)
    common = dict(mode=mode, args=args, min_decode_snr=min_snr,
                  restart_after_silent_sec=parse_duration(ras) if ras else 0)

    use_sdr = False
    if cp.has_option(section, "sdr"):
        try:
            use_sdr = cp.getboolean(section, "sdr")
        except ValueError as e:
            raise ConfigError(
                f"[{section}] sdr must be yes/true (named SDRs not yet supported)"
            ) from e

    present = [k for k, v in (("card", card), ("path", path),
                              ("input", inp), ("sdr", use_sdr or None)) if v]
    if len(present) != 1:
        raise ConfigError(
            f"[{section}] needs exactly one of card/path/input/sdr "
            f"(got {present or 'none'})"
        )

    if card is not None:
        device, channel = _parse_card(section, card)
        return Receiver(name, band, freq, "audio",
                        card=device, channel=channel, **common)
    if path is not None:
        return Receiver(name, band, freq, "file",
                        path=os.path.expanduser(path), **common)
    if use_sdr:
        if sdr is None:
            raise ConfigError(f"[{section}] sdr = yes requires an [sdr] section")
        fifo = os.path.join(sdr.runtime_dir, f"{name}.fifo")
        return Receiver(name, band, freq, "sdr", path=fifo, **common)

    inp = inp.lower()
    if inp not in SDR_INPUTS:
        raise ConfigError(f"[{section}] unknown input '{inp}' (one of {SDR_INPUTS})")
    serial = cp.get(section, "serial", fallback=None)
    ip = cp.get(section, "ip", fallback=None)
    if inp in SDR_NEEDS_IP and not ip:
        raise ConfigError(f"[{section}] input '{inp}' requires ip")
    return Receiver(name, band, freq, "backend",
                    input=inp, serial=serial, ip=ip, **common)


def _sdr(cp: configparser.ConfigParser, storage: Storage) -> Optional[Sdr]:
    if not cp.has_section("sdr"):
        return None
    default_runtime = os.path.join(os.path.expanduser(storage.dir), "run")
    runtime = cp.get("sdr", "runtime_dir", fallback=default_runtime)
    return Sdr(
        runtime_dir=os.path.expanduser(runtime),
        driver=cp.get("sdr", "driver", fallback=""),
        gain=cp.get("sdr", "gain", fallback=None),
        rate=cp.getint("sdr", "rate", fallback=None),
        center=cp.getint("sdr", "center", fallback=None),
        guard=cp.getint("sdr", "guard", fallback=10000),
        ppm=cp.getfloat("sdr", "ppm", fallback=0.0),
        antenna=cp.get("sdr", "antenna", fallback=None),
        buffer=cp.getfloat("sdr", "buffer", fallback=1.0),
        path=os.path.expanduser(cp.get("sdr", "path", fallback="sdrfanout")),
    )


def _parse_card(section: str, card: str) -> tuple[int, int]:
    parts = re.split(r"[:\s]+", card.strip())
    try:
        device = int(parts[0])
        channel = int(parts[1]) if len(parts) > 1 else 0
    except ValueError as e:
        raise ConfigError(f"[{section}] invalid card {card!r} (want N or N:CH)") from e
    return device, channel


def _collector(cp: configparser.ConfigParser) -> Collector:
    addr = cp.get("collector", "address", fallback=None)
    if not addr:
        raise ConfigError("[collector] address is required")
    delivery = cp.get("collector", "delivery", fallback="direct").lower()
    if delivery not in ("direct", "propagated"):
        raise ConfigError(f"[collector] delivery must be direct or propagated, got {delivery!r}")
    node = cp.get("collector", "propagation_node", fallback=None)
    if delivery == "propagated" and not node:
        raise ConfigError("[collector] delivery=propagated requires propagation_node")
    return Collector(
        address=addr,
        send_interval=cp.getint("collector", "send_interval", fallback=120),
        delivery=delivery,
        propagation_node=node,
        max_pending_observations=cp.getint(
            "collector", "max_pending_observations", fallback=50000
        ),
        send_empty_batches=cp.getboolean(
            "collector", "send_empty_batches", fallback=False
        ),
    )
