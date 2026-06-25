"""Tie WSPR parsing and observation extraction together.

Parallel to ``ft8_pipeline.py`` but simpler: WSPR messages carry their grid and
wsprd resolves callsign hashes internally, so there is no callsign cache and no
indirect path. ``ingest`` is the per-line entry the monitor dispatches WSPR
receivers to.
"""

from __future__ import annotations

from typing import Optional

from .observations import Observation
from .wspr_parser import classify, extract, parse_line


def ingest(
    line: str,
    dial_hz: int,
    monitor_grid: str,
    monitor_call: Optional[str] = None,
    min_snr: int = -1000,
) -> Optional[list[tuple[Observation, int]]]:
    """Parse one wsprmon line into (observation, freq_hz) pairs for the monitor.

    Returns None for a non-decode line, a list for a decode (empty when the
    message carried no grid, or its SNR is below ``min_snr``). wsprmon runs in
    offset mode (-hz, no -f), so the decode carries an audio offset and the
    absolute frequency is the dial plus that offset, the same as FT8.
    """
    decode = parse_line(line)
    if decode is None:
        return None
    if decode.snr < min_snr:
        return []                       # too weak to trust
    spot = classify(decode.message)
    if spot is None:
        return []                       # decode with no grid (type 2): nothing to store
    freq_hz = dial_hz + int(round(decode.freq_hz))
    obs = extract(spot, decode.snr, monitor_grid, monitor_call)
    return [(o, freq_hz) for o in obs]
