"""Load and validate the monitor's INI configuration.

Only ``[monitor] grid``, at least one enabled ``[receiver:*]``, and
``[collector] address`` are required; everything else has a default. Each
``[receiver:NAME]`` section is one receiver process: NAME is its unique id,
``band`` defaults to NAME, ``mode`` is ``ft8`` (default) or ``wspr``, and
``enabled = false`` keeps a section configured but not running. Its input is
one of ``card`` (audio device), ``path`` (WAV file), or ``input`` (SDR).
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
    kind: str                       # "audio" | "file" | "sdr"
    mode: str = "ft8"               # "ft8" | "wspr"
    card: Optional[int] = None
    channel: int = 0
    path: Optional[str] = None
    input: Optional[str] = None
    serial: Optional[str] = None
    ip: Optional[str] = None
    args: list = field(default_factory=list)

    def _source_args(self) -> list[str]:
        """The ``-card …`` input selector shared by ft8mon and wsprmon."""
        if self.kind == "audio":
            return ["-card", str(self.card), str(self.channel)]
        if self.kind == "file":
            return ["-card", "file", self.path]
        ident = (self.serial or "") if self.input == "airspy" else self.ip
        return ["-card", self.input, f"{ident},{_mhz(self.freq)}"]

    def ft8mon_args(self) -> list[str]:
        """Arguments this receiver launches ft8mon with."""
        return [*self._source_args(), *self.args]

    def wsprmon_args(self, wsprd_path: Optional[str] = None,
                     workdir: Optional[str] = None) -> list[str]:
        """Arguments this receiver launches wsprmon with.

        wsprmon takes the dial via ``-f`` and reads a file via ``-file`` (last,
        as it consumes the rest); audio/SDR reuse the shared ``-card`` selector.
        ``workdir`` (``-a``) is where wsprmon writes its per-cycle wav; each
        receiver gets its own so concurrent receivers never collide.
        """
        args: list[str] = []
        if wsprd_path:
            args += ["-wsprd", wsprd_path]
        if workdir:
            args += ["-a", workdir]
        args += ["-f", _mhz(self.freq)]
        if self.kind == "file":
            args += [*self.args, "-file", self.path]
        else:
            args += [*self._source_args(), *self.args]
        return args


# --- config sections ------------------------------------------------------

@dataclass
class Monitor:
    grid: str
    lat: float
    lon: float


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
    send_interval: int = 60
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
    restart_after_silent_cycles: int = 0   # 0 disables the no-decode watchdog
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
    receivers = _receivers(cp)
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
    storage = Storage(dir=cp.get("storage", "dir", fallback="~/.watchersattherim"))
    reticulum = Reticulum(config_dir=cp.get("reticulum", "config_dir", fallback=None))

    wsprd_path = cp.get("wsprmon", "wsprd_path", fallback=None)
    return Config(
        monitor=monitor,
        receivers=receivers,
        collector=collector,
        ft8mon_path=os.path.expanduser(cp.get("ft8mon", "path", fallback="ft8mon")),
        wsprmon_path=os.path.expanduser(cp.get("wsprmon", "path", fallback="wsprmon")),
        wsprd_path=os.path.expanduser(wsprd_path) if wsprd_path else None,
        restart_after_silent_cycles=cp.getint(
            "ft8mon", "restart_after_silent_cycles", fallback=0
        ),
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
    return Monitor(grid=grid, lat=lat, lon=lon)


def _receivers(cp: configparser.ConfigParser) -> list[Receiver]:
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
        receivers.append(_receiver(cp, section, name))
    if not any_section:
        raise ConfigError("at least one [receiver:NAME] section is required")
    if not receivers:
        raise ConfigError("no enabled [receiver:NAME] sections (all enabled=false)")
    return receivers


def _receiver(cp, section: str, name: str) -> Receiver:
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

    present = [k for k, v in (("card", card), ("path", path), ("input", inp)) if v]
    if len(present) != 1:
        raise ConfigError(
            f"[{section}] needs exactly one of card/path/input (got {present or 'none'})"
        )

    if card is not None:
        device, channel = _parse_card(section, card)
        return Receiver(name, band, freq, "audio", mode=mode,
                        card=device, channel=channel, args=args)
    if path is not None:
        return Receiver(name, band, freq, "file", mode=mode,
                        path=os.path.expanduser(path), args=args)

    inp = inp.lower()
    if inp not in SDR_INPUTS:
        raise ConfigError(f"[{section}] unknown input '{inp}' (one of {SDR_INPUTS})")
    serial = cp.get(section, "serial", fallback=None)
    ip = cp.get(section, "ip", fallback=None)
    if inp in SDR_NEEDS_IP and not ip:
        raise ConfigError(f"[{section}] input '{inp}' requires ip")
    return Receiver(name, band, freq, "sdr", mode=mode,
                    input=inp, serial=serial, ip=ip, args=args)


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
        send_interval=cp.getint("collector", "send_interval", fallback=60),
        delivery=delivery,
        propagation_node=node,
        max_pending_observations=cp.getint(
            "collector", "max_pending_observations", fallback=50000
        ),
        send_empty_batches=cp.getboolean(
            "collector", "send_empty_batches", fallback=False
        ),
    )
