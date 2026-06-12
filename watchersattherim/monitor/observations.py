"""Turn classified decodes into propagation observations.

An observation is a single ``(loc_a, loc_b, snr)`` path sample. Two sources,
equally valid:

* **direct** - a path terminating at this monitor. We decoded a transmission, so
  we heard the transmitter: TX = transmitting station (``call_de``), RX = this
  monitor, SNR = our decode's SNR. The transmitter's grid comes from the message
  or the callsign cache.

* **indirect** - a path between two other stations, recovered from a signal
  report carried in a message we overheard. In ``CALL_TO CALL_DE <report>`` the
  transmitter CALL_DE is reporting how well it heard CALL_TO, so the measured
  path is TX = CALL_TO, RX = CALL_DE, SNR = report (per the WSJT-X QEX paper and ft8_lib).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Protocol

# 4- or 6-character Maidenhead locator (field A-R, square 0-9, subsquare A-X).
_GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$")


# ---------------------------------------------------------------------------
# Maidenhead grid -> lat/lon (grid center)
# ---------------------------------------------------------------------------

def grid_to_latlon(grid: str) -> tuple[float, float]:
    """Convert a 4- or 6-char Maidenhead locator to (lat, lon) of its center."""
    g = grid.strip().upper()
    if not _GRID_RE.match(g):
        raise ValueError(f"invalid Maidenhead locator: {grid!r}")

    lon = -180.0 + (ord(g[0]) - ord("A")) * 20.0 + int(g[2]) * 2.0
    lat = -90.0 + (ord(g[1]) - ord("A")) * 10.0 + int(g[3]) * 1.0

    if len(g) == 6:
        lon += (ord(g[4]) - ord("A")) * (2.0 / 24.0)
        lat += (ord(g[5]) - ord("A")) * (1.0 / 24.0)
        lon += (2.0 / 24.0) / 2.0   # center of subsquare
        lat += (1.0 / 24.0) / 2.0
    else:
        lon += 1.0                   # center of 2-degree square
        lat += 0.5                   # center of 1-degree square

    return round(lat, 4), round(lon, 4)


# ---------------------------------------------------------------------------
# Callsign -> grid cache (minimal; full LRU/TTL/persist is a later component)
# ---------------------------------------------------------------------------

class GridLookup(Protocol):
    def __call__(self, callsign: str) -> Optional[str]: ...


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Observation:
    kind: str            # "direct" | "indirect"
    tx_grid: str
    tx_lat: float
    tx_lon: float
    rx_grid: str
    rx_lat: float
    rx_lon: float
    snr_db: int
    tx_call: Optional[str] = None
    rx_call: Optional[str] = None


def _obs(kind, tx_call, tx_grid, rx_call, rx_grid, snr) -> Observation:
    tlat, tlon = grid_to_latlon(tx_grid)
    rlat, rlon = grid_to_latlon(rx_grid)
    return Observation(
        kind=kind,
        tx_grid=tx_grid, tx_lat=tlat, tx_lon=tlon,
        rx_grid=rx_grid, rx_lat=rlat, rx_lon=rlon,
        snr_db=snr, tx_call=tx_call, rx_call=rx_call,
    )


def extract(
    msg,                                  # watchers.parser.Message
    decode_snr: int,
    monitor_grid: str,
    monitor_call: Optional[str] = None,
    lookup: Optional[GridLookup] = None,
) -> list[Observation]:
    """Produce 0..2 observations from one classified decode.

    ``lookup(callsign) -> grid | None`` backfills grids from the callsign cache;
    pass ``None`` to use only grids present in the message.
    """
    from .parser import Kind

    if lookup is None:
        def lookup(_call: str) -> Optional[str]:  # noqa: ANN202
            return None

    out: list[Observation] = []

    # The transmitter is call_de (CQ caller for a CQ). Direct path ends here.
    tx_call = msg.call_de
    if tx_call is not None and not msg.de_hashed:
        tx_grid = msg.grid or lookup(tx_call)
        if tx_grid is not None:
            out.append(_obs("direct", tx_call, tx_grid,
                            monitor_call, monitor_grid, decode_snr))

    # Indirect: report given by call_de about call_to -> path call_to -> call_de.
    if (
        msg.kind in (Kind.STANDARD, Kind.NONSTD)
        and msg.report_db is not None
        and msg.call_to is not None and not msg.to_hashed
        and msg.call_de is not None and not msg.de_hashed
    ):
        a_grid = lookup(msg.call_to)
        b_grid = lookup(msg.call_de)
        if a_grid is not None and b_grid is not None:
            out.append(_obs("indirect", msg.call_to, a_grid,
                            msg.call_de, b_grid, msg.report_db))

    return out
