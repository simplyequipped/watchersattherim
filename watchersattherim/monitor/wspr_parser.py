"""Parse wsprmon stdout into WSPR decodes and spots.

Self-contained (stdlib only), parallel to ``parser.py`` (FT8), so it can be
tested in isolation against captured wsprmon output.

wsprmon emits one line per decode::

    HHMMSS  SNR  DT  FREQ_MHZ  DRIFT  MESSAGE

plus a ``HH:MM:SS decodes: N`` cycle header that is ignored (its colons keep it
from matching the all-digit timestamp). ``parse_line`` turns a decode line into
a ``WsprDecode`` (the fixed columns + raw message); ``classify`` turns the
message into a ``WsprSpot``.

wsprd does all callsign-hash bookkeeping internally, so the message is already
one of three shapes and we never reason about WSPR "type" explicitly:

    CALL GRID4 POWER        keep  (grid present)
    <CALL> GRID6 POWER      keep  (grid present; call resolved, or <...> if not)
    CALL POWER              drop  (no grid)

The rule is simply: keep any message that carries a grid. A dropped (grid-less)
message loses no station - that station's grid arrives on its own gridded line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Optional

from .observations import Observation, make_observation

# wsprmon decode line, e.g.:
#   091800  -9  1.1  14.097046  0  ND6P FN19 30
# columns: HHMMSS  SNR  DT  FREQ_MHz  DRIFT  message
_DECODE_RE = re.compile(
    r"^(?P<ts>\d{6})\s+"
    r"(?P<snr>-?\d+)\s+"
    r"(?P<dt>-?\d+\.\d+)\s+"
    r"(?P<freq>\d+\.\d+)\s+"
    r"(?P<drift>-?\d+)\s+"
    r"(?P<msg>.*)$"
)

# 4- or 6-character Maidenhead locator.
_GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$")


@dataclass(frozen=True)
class WsprDecode:
    """One raw wsprmon decode line."""

    hhmmss: str          # "091800", UTC, start of the 2 min slot
    snr: int             # dB
    dt: float            # seconds, time offset of the signal in the slot
    freq_mhz: float      # MHz, absolute (dial + audio offset)
    drift: int           # Hz/minute
    message: str         # raw WSPR message, whitespace-trimmed


@dataclass(frozen=True)
class WsprSpot:
    """A WSPR message that carries a transmitter location."""

    grid: str                    # 4- or 6-char Maidenhead (always present)
    power_dbm: int               # reported transmit power
    call: Optional[str] = None   # resolved call, or None if hashed/unknown
    hashed: bool = False         # call arrived wrapped in <...> (compound call)


def parse_line(line: str) -> Optional[WsprDecode]:
    """Parse one line of wsprmon stdout. Returns ``None`` for non-decode lines."""
    m = _DECODE_RE.match(line)
    if not m:
        return None
    return WsprDecode(
        hhmmss=m["ts"],
        snr=int(m["snr"]),
        dt=float(m["dt"]),
        freq_mhz=float(m["freq"]),
        drift=int(m["drift"]),
        message=m["msg"].strip(),
    )


def parse_stream(lines: Iterator[str]) -> Iterator[WsprDecode]:
    """Yield a ``WsprDecode`` for every decode line in an iterable of lines."""
    for line in lines:
        d = parse_line(line)
        if d is not None:
            yield d


def classify(message: str) -> Optional[WsprSpot]:
    """Turn a WSPR message into a ``WsprSpot``, or ``None`` if it carries no grid."""
    toks = message.split()
    if len(toks) < 3:
        return None                       # CALL POWER (no grid), or junk
    try:
        power = int(toks[-1])
    except ValueError:
        return None
    grid = toks[-2]
    if not _GRID_RE.match(grid):
        return None                       # nothing in the grid slot -> drop

    call_tok = toks[0]
    if call_tok.startswith("<"):
        inner = call_tok.strip("<>")      # "<PJ4/K1ABC>" -> resolved; "<...>" -> unknown
        return WsprSpot(grid=grid, power_dbm=power,
                        call=None if inner == "..." else inner, hashed=True)
    return WsprSpot(grid=grid, power_dbm=power, call=call_tok, hashed=False)


def extract(
    spot: WsprSpot,
    decode_snr: int,
    monitor_grid: str,
    monitor_call: Optional[str] = None,
) -> list[Observation]:
    """One direct observation for a WSPR spot: TX at the spot's grid, RX here.

    WSPR has no indirect (overheard-report) path, and a kept spot always carries
    a grid, so this is always exactly one observation - with the TX power.
    """
    return [make_observation(
        "direct",
        spot.call, spot.grid,
        monitor_call, monitor_grid,
        decode_snr,
        power_dbm=spot.power_dbm,
    )]
