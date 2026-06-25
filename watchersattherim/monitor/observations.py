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

import math
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


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    r1, r2 = math.radians(lat1), math.radians(lat2)
    cos_d = (math.sin(r1) * math.sin(r2)
             + math.cos(r1) * math.cos(r2) * math.cos(math.radians(lon2 - lon1)))
    return 6371.0 * math.acos(max(-1.0, min(1.0, cos_d)))


def snr_exceeds_ceiling(ceiling, distance_km: float, snr: int) -> bool:
    """True if ``snr`` is above the plausibility ceiling that applies at ``distance_km``.

    ``ceiling`` is a sorted tuple of ``(distance_km, max_snr_db)`` pairs. The cap is
    the pair with the largest distance not exceeding ``distance_km``; below the
    smallest listed distance there is no cap. Used to drop strong-at-distance
    decodes (physically impossible, so fabricated) while keeping weak long-haul.
    """
    cap = None
    for d, s in ceiling:
        if distance_km >= d:
            cap = s
        else:
            break
    return cap is not None and snr > cap


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
    power_dbm: Optional[int] = None   # WSPR transmit power; None for FT8


def make_observation(kind, tx_call, tx_grid, rx_call, rx_grid, snr,
                     power_dbm=None) -> Observation:
    tlat, tlon = grid_to_latlon(tx_grid)
    rlat, rlon = grid_to_latlon(rx_grid)
    return Observation(
        kind=kind,
        tx_grid=tx_grid, tx_lat=tlat, tx_lon=tlon,
        rx_grid=rx_grid, rx_lat=rlat, rx_lon=rlon,
        snr_db=snr, tx_call=tx_call, rx_call=rx_call,
        power_dbm=power_dbm,
    )
