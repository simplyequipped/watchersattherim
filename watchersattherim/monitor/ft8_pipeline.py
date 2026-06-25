"""Tie parsing, the callsign cache, and observation extraction together.

``process_decode`` is the per-decode step the monitor wrapper runs: it learns any
grid the decode reveals, then extracts observations using the cache to backfill
grids.
"""

from __future__ import annotations

from typing import Optional

from .cache import CallsignCache
from .observations import Observation
from .ft8_parser import Decode, Message, classify, extract, parse_line


def learn(msg: Message, cache: CallsignCache, ts: Optional[float] = None) -> None:
    """Record any (callsign, grid) the message reveals into the cache."""
    # The grid in a message belongs to the transmitter (call_de / CQ caller).
    if msg.grid and msg.call_de and not msg.de_hashed:
        cache.update(msg.call_de, msg.grid, ts)


def process_decode(
    decode: Decode,
    msg: Message,
    monitor_grid: str,
    cache: CallsignCache,
    monitor_call: Optional[str] = None,
    ts: Optional[float] = None,
) -> list[Observation]:
    """Learn from, then extract observations for, one classified decode."""
    learn(msg, cache, ts)
    return extract(
        msg,
        decode_snr=decode.snr,
        monitor_grid=monitor_grid,
        monitor_call=monitor_call,
        lookup=lambda c: cache.lookup(c, ts),
    )


def handle_line(
    line: str,
    cache: CallsignCache,
    monitor_grid: str,
    monitor_call: Optional[str] = None,
    ts: Optional[float] = None,
) -> list[Observation]:
    """Per-line entry point for the driver.

    Parses one line of ft8mon stdout; non-decode lines yield no observations.
    """
    decode = parse_line(line)
    if decode is None:
        return []
    msg = classify(decode.message)
    return process_decode(decode, msg, monitor_grid, cache, monitor_call, ts)


def ingest(
    line: str,
    dial_hz: int,
    monitor_grid: str,
    cache: CallsignCache,
    monitor_call: Optional[str] = None,
    ts: Optional[float] = None,
    min_snr: int = -1000,
) -> Optional[list[tuple[Observation, int]]]:
    """Parse one ft8mon line into (observation, freq_hz) pairs for the monitor.

    Returns None for a non-decode line; a list (possibly empty) for a decode.
    FT8 frequency is the dial plus the decode's audio offset. A decode whose SNR is
    below ``min_snr`` still counts as a decode (the receiver is alive) but yields no
    observations. Too weak to trust, and it would poison the dataset.
    """
    decode = parse_line(line)
    if decode is None:
        return None
    if decode.snr < min_snr:
        return []
    freq_hz = dial_hz + int(round(decode.freq))
    obs = process_decode(decode, classify(decode.message), monitor_grid, cache,
                         monitor_call, ts)
    return [(o, freq_hz) for o in obs]
