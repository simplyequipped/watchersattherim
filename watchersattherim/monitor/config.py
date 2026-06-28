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
import subprocess
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
    card: Optional[int] = None      # audio device number, resolved from card_desc when needed
    card_desc: Optional[str] = None # text to match against -list device names, until resolved
    channel: int = 0
    path: Optional[str] = None      # WAV path (file), or the FIFO path (sdr)
    input: Optional[str] = None
    serial: Optional[str] = None
    ip: Optional[str] = None
    min_decode_snr: int = -25            # drop this receiver's decodes below this SNR
    restart_after_silent_sec: int = 0    # 0 = no-decode watchdog disabled for this receiver
    snr_ceiling: tuple = ()              # ((distance_km, max_snr), ...) sorted; drop strong-at-distance

    def _source_args(self) -> list[str]:
        """The ``-card …`` input selector shared by ft8mon and wsprmon."""
        if self.kind == "audio":
            if self.card is None:
                raise ConfigError(
                    f"[receiver:{self.name}] card {self.card_desc!r} was not resolved "
                    f"to a device number"
                )
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
        return self._source_args()

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
            args += ["-file", self.path]
        else:
            args += self._source_args()
        return args


# --- shared SDR (sdrfanout) -----------------------------------------------

@dataclass
class Sdr:
    """One physical SDR, fanned out to its ``sdr = yes`` receivers by sdrfanout.

    Device settings apply to the whole radio (all channels). Only a receiver's
    dial (``freq``) and FIFO are per channel. ``working_dir`` is where the monitor
    creates the channel FIFOs.
    """
    working_dir: str                 # resolved/expanded scratch dir, defaults to [monitor] working_dir
    driver: str = ""                 # device name, e.g. "hackrf". "" = first device
    gain: Optional[str] = None       # dB string, or None/"auto" = device default
    rate: Optional[int] = None       # Hz. None = auto (smallest int x12k spanning channels)
    center: "int | str | None" = None  # Hz, "edge", or None = auto (LO centered in the channels)
    guard: int = 10000               # Hz a channel must clear the LO and each band edge
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
    working_dir: str = "" # scratch base for FIFOs and wsprmon wavs; a tmpfs by default


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
    send_interval: int = 300        # seconds between batches (accepts "5m" etc.)
    min_send_interval: int = 60      # floor send_interval may not go below
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


_FREQ_TOL_HZ = 10   # a bare blacklist freq matches within this many Hz (decoder jitter)


@dataclass(frozen=True)
class Blacklist:
    """Decodes to drop at the monitor before they become observations.

    Matches are global (every receiver). ``grids`` and ``calls`` match the
    transmitter's. ``freqs`` are absolute RF frequencies (the dial plus the decode's
    audio offset) as inclusive (lo, hi) Hz ranges, so an entry targets one band's
    birdie without touching receivers on other bands.
    """
    grids: frozenset[str] = frozenset()
    calls: frozenset[str] = frozenset()
    freqs: tuple[tuple[int, int], ...] = ()

    def blocks(self, tx_grid: str, tx_call: Optional[str], freq_hz: int) -> bool:
        if tx_grid and tx_grid.upper() in self.grids:
            return True
        if tx_call and tx_call.upper() in self.calls:
            return True
        return any(lo <= freq_hz <= hi for lo, hi in self.freqs)


@dataclass
class Config:
    monitor: Monitor
    receivers: list[Receiver]
    collector: Collector
    ft8mon_path: str = "ft8mon"
    wsprmon_path: str = "wsprmon"
    wsprd_path: Optional[str] = None        # None: wsprmon self-resolves wsprd
    wsprmon_working_dir: str = ""           # base dir for wsprmon's wav scratch
    sdr: Optional[Sdr] = None               # the shared SDR. None when no [sdr] section
    observations: Observations = field(default_factory=Observations)
    cache: Cache = field(default_factory=Cache)
    storage: Storage = field(default_factory=Storage)
    reticulum: Reticulum = field(default_factory=Reticulum)
    blacklist: Blacklist = field(default_factory=Blacklist)


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
    # storage first: the working dirs fall back under it. monitor.working_dir is the
    # scratch base the [sdr] FIFO dir and the wsprmon wav dir default to, so both the
    # SDR (FIFO paths) and the wsprmon receivers resolve against it.
    storage = Storage(dir=cp.get("storage", "dir", fallback="~/.watchersattherim"))
    monitor = _monitor(cp, storage)
    sdr = _sdr(cp, monitor.working_dir)
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
    wsprmon_wd = cp.get("wsprmon", "working_dir", fallback=None)
    return Config(
        blacklist=_blacklist(cp),
        monitor=monitor,
        receivers=receivers,
        collector=collector,
        sdr=sdr,
        ft8mon_path=os.path.expanduser(cp.get("ft8mon", "path", fallback="ft8mon")),
        wsprmon_path=os.path.expanduser(cp.get("wsprmon", "path", fallback="wsprmon")),
        wsprd_path=os.path.expanduser(wsprd_path) if wsprd_path else None,
        wsprmon_working_dir=os.path.expanduser(wsprmon_wd) if wsprmon_wd else monitor.working_dir,
        observations=obs,
        cache=cache,
        storage=storage,
        reticulum=reticulum,
    )


def _default_working_dir(storage: Storage) -> str:
    """The default scratch base for ``[monitor] working_dir``.

    A tmpfs under ``/dev/shm`` so transient files (the channel FIFOs and wsprmon's
    per-slot wav) stay in RAM and never wear an SD card. Falls back to
    ``<storage.dir>`` where /dev/shm is absent or not writable (containers, minimal
    installs).
    """
    shm = "/dev/shm"
    if os.path.isdir(shm) and os.access(shm, os.W_OK):
        return os.path.join(shm, "watchersattherim")
    return os.path.expanduser(storage.dir)


def _monitor(cp: configparser.ConfigParser, storage: Storage) -> Monitor:
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
    wd = cp.get("monitor", "working_dir", fallback=None)
    working_dir = os.path.expanduser(wd) if wd else _default_working_dir(storage)
    return Monitor(grid=grid, lat=lat, lon=lon, debug=debug, working_dir=working_dir)


def _blacklist(cp: configparser.ConfigParser) -> Blacklist:
    def items(key: str) -> list[str]:
        raw = cp.get("blacklist", key, fallback="").strip()
        return [t for t in re.split(r"[,\s]+", raw) if t]

    freqs: list[tuple[int, int]] = []
    for tok in items("freqs"):
        try:
            if "-" in tok:
                lo, hi = tok.split("-", 1)
                freqs.append((int(lo), int(hi)))
            else:
                v = int(tok)
                freqs.append((v - _FREQ_TOL_HZ, v + _FREQ_TOL_HZ))
        except ValueError:
            raise ConfigError(
                f"[blacklist] freqs: invalid entry {tok!r} (use Hz values or lo-hi ranges)"
            ) from None
    return Blacklist(
        grids=frozenset(g.upper() for g in items("grids")),
        calls=frozenset(c.upper() for c in items("callsigns")),
        freqs=tuple(freqs),
    )


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


_MI_TO_KM = 1.609344


def _parse_snr_ceiling(cp, section: str) -> tuple:
    """Parse a receiver's distance:snr plausibility ceiling from km and/or mi keys.

    Each `distance:snr` pair caps the SNR allowed at or beyond that distance (a
    strong decode past it is physically impossible, so fabricated). mi distances are
    converted to km and merged. Returns the pairs sorted by distance ascending.
    """
    pairs: list[tuple[int, int]] = []
    for key, factor in (("distance_snr_threshold_km", 1.0),
                        ("distance_snr_threshold_mi", _MI_TO_KM)):
        raw = cp.get(section, key, fallback="").strip()
        for tok in re.split(r"[,\s]+", raw):
            if not tok:
                continue
            try:
                dist, snr = tok.split(":", 1)
                pairs.append((int(round(int(dist) * factor)), int(snr)))
            except ValueError:
                raise ConfigError(
                    f"[{section}] {key}: invalid entry {tok!r} (use distance:snr pairs)"
                ) from None
    return tuple(sorted(pairs))


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

    # drop weak decodes: default just below each mode's floor (FT8 ~-24, WSPR ~-28)
    min_snr = cp.getint(section, "min_decode_snr",
                        fallback=-25 if mode == "ft8" else -30)
    ras = cp.get(section, "restart_after_silent", fallback=None)
    common = dict(mode=mode, min_decode_snr=min_snr,
                  restart_after_silent_sec=parse_duration(ras) if ras else 0,
                  snr_ceiling=_parse_snr_ceiling(cp, section))

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
        device, desc, channel = _parse_card(section, card)
        return Receiver(name, band, freq, "audio",
                        card=device, card_desc=desc, channel=channel, **common)
    if path is not None:
        return Receiver(name, band, freq, "file",
                        path=os.path.expanduser(path), **common)
    if use_sdr:
        if sdr is None:
            raise ConfigError(f"[{section}] sdr = yes requires an [sdr] section")
        fifo = os.path.join(sdr.working_dir, "sdrfanout", f"{name}.fifo")
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


def _sdr_center(cp: configparser.ConfigParser) -> "int | str | None":
    """Parse ``[sdr] center``: a frequency in Hz, ``edge``, or ``auto``/unset.

    ``edge`` selects sdrfanout's one-sided layout (LO just below all channels).
    ``auto`` or unset leaves it to sdrfanout's default (LO centered in the channels).
    """
    raw = cp.get("sdr", "center", fallback=None)
    if raw is None:
        return None
    v = raw.strip().lower()
    if v in ("", "auto"):
        return None
    if v == "edge":
        return "edge"
    try:
        return int(v)
    except ValueError:
        raise ConfigError(
            f"[sdr] center must be a frequency in Hz, 'edge', or 'auto', got {raw!r}"
        ) from None


def _sdr(cp: configparser.ConfigParser, monitor_working_dir: str) -> Optional[Sdr]:
    if not cp.has_section("sdr"):
        return None
    wd = cp.get("sdr", "working_dir", fallback=None)
    working_dir = os.path.expanduser(wd) if wd else monitor_working_dir
    return Sdr(
        working_dir=working_dir,
        driver=cp.get("sdr", "driver", fallback=""),
        gain=cp.get("sdr", "gain", fallback=None),
        rate=cp.getint("sdr", "rate", fallback=None),
        center=_sdr_center(cp),
        guard=cp.getint("sdr", "guard", fallback=10000),
        ppm=cp.getfloat("sdr", "ppm", fallback=0.0),
        antenna=cp.get("sdr", "antenna", fallback=None),
        buffer=cp.getfloat("sdr", "buffer", fallback=1.0),
        path=os.path.expanduser(cp.get("sdr", "path", fallback="sdrfanout")),
    )


def _parse_card(section: str, card: str) -> tuple[Optional[int], Optional[str], int]:
    """Split a ``card`` value into ``(device, description, channel)``.

    A trailing ``:CH`` (CH an integer) is the audio channel, default 0. What
    remains is the device: an integer is its ``-list`` number, anything else is a
    text description to match against the ``-list`` device names (resolved later by
    running the decoder's ``-list``). Exactly one of device/description is set.
    """
    body = card.strip()
    channel = 0
    if ":" in body:
        head, tail = body.rsplit(":", 1)
        try:
            channel = int(tail)
            body = head.strip()
        except ValueError:
            pass   # not a channel suffix, so the colon is part of a description
    if not body:
        raise ConfigError(f"[{section}] invalid card {card!r} (want N, N:CH, or a description)")
    try:
        return int(body), None, channel
    except ValueError:
        return None, body, channel


def parse_device_list(text: str) -> list[tuple[int, str]]:
    """Parse ``ft8mon -list`` / ``wsprmon -list`` output into ``(device, name)`` pairs.

    Each device line is ``N: NAME IN/OUT <rates...>`` (PortAudio's enumeration), so
    NAME is the text between the device number and the ``IN/OUT`` channel counts.
    """
    devices: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+):\s+(.+?)\s+\d+/\d+", line)
        if m:
            devices.append((int(m.group(1)), m.group(2).strip()))
    return devices


def resolve_card_desc(section: str, desc: str,
                      devices: list[tuple[int, str]]) -> int:
    """Match a card description to exactly one device number from a parsed ``-list``.

    The match is a case-insensitive substring of the device name. No match or more
    than one match is a config error.
    """
    matches = [(n, name) for n, name in devices if desc.lower() in name.lower()]
    if not matches:
        listed = ", ".join(f"{n} ({name})" for n, name in devices) or "none"
        raise ConfigError(
            f"[{section}] card {desc!r} matched no audio device (listed: {listed})"
        )
    if len(matches) > 1:
        names = ", ".join(f"{n} ({name})" for n, name in matches)
        raise ConfigError(
            f"[{section}] card {desc!r} matched multiple audio devices: {names}"
        )
    return matches[0][0]


def list_audio_devices(binary_path: str) -> list[tuple[int, str]]:
    """Run ``<binary_path> -list`` and return its parsed ``(device, name)`` pairs."""
    try:
        proc = subprocess.run([binary_path, "-list"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise ConfigError(f"could not run {binary_path} -list: {e}") from e
    return parse_device_list(proc.stdout)


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
    send_interval = parse_duration(cp.get("collector", "send_interval", fallback="5m"))
    min_send_interval = parse_duration(cp.get("collector", "min_send_interval", fallback="60"))
    if send_interval < min_send_interval:
        raise ConfigError(
            f"[collector] send_interval ({send_interval}s) is below "
            f"min_send_interval ({min_send_interval}s)"
        )
    return Collector(
        address=addr,
        send_interval=send_interval,
        min_send_interval=min_send_interval,
        delivery=delivery,
        propagation_node=node,
        max_pending_observations=cp.getint(
            "collector", "max_pending_observations", fallback=50000
        ),
        send_empty_batches=cp.getboolean(
            "collector", "send_empty_batches", fallback=False
        ),
    )
