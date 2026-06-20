"""map and coverage: per-cell fields over an area.

map is band activity over space (origin-agnostic, single mode). coverage is the
channel estimate run per cell from (or to) a fixed endpoint - symmetric per mode
like channel, each cell carrying an ``ft8`` and a ``wspr`` object (the best band
for that mode, or ``null``). Both return only cells that have observations; there
is no interpolation into empty cells.
"""

from __future__ import annotations

import statistics
import time
from typing import Optional

from ..common.config import fmt_duration
from . import geo, metrics
from .channel import DEFAULT_REF_POWER_DBM

DEFAULT_MAP_WINDOW_SEC = 3600
DEFAULT_COVERAGE_WINDOW_SEC = 1800
DEFAULT_RADIUS_KM = 2000
_MODES = ("ft8", "wspr")


def map_field(conn, *, origin: str, radius_km: float = DEFAULT_RADIUS_KM,
              units: str = "km", band: str = "40m", mode: str = "FT8",
              resolution: str = "medium", window_sec: int = DEFAULT_MAP_WINDOW_SEC,
              at: Optional[int] = None, max_cells: int = 2000) -> dict:
    origin = origin.upper()
    at = int(time.time()) if at is None else int(at)
    center = geo.grid_center(origin)
    precision = geo.resolution_chars(resolution)
    rows = conn.execute(
        "SELECT snr_db, tx_grid FROM observations "
        "WHERE band = ? AND mode = ? AND observed_at >= ?",
        (band, mode.upper(), at - window_sec),
    ).fetchall()

    cells: dict[str, list] = {}
    for r in rows:
        cell = r["tx_grid"][:precision]
        clat, clon = geo.grid_center(cell)
        if geo.haversine_km(center[0], center[1], clat, clon) > radius_km:
            continue
        cells.setdefault(cell, []).append(r["snr_db"])

    out = []
    for cell, snrs in cells.items():
        clat, clon = geo.grid_center(cell)
        median_snr = round(statistics.median(snrs), 1)
        out.append({
            "grid": cell, "lat": clat, "lon": clon,
            "distance": geo.convert_km(geo.haversine_km(center[0], center[1], clat, clon), units),
            "bearing": geo.bearing(center[0], center[1], clat, clon),
            "quality": metrics.quality(median_snr, mode.upper()),
            "median_snr_db": median_snr,
            "observations": len(snrs),
        })
    out.sort(key=lambda c: c["observations"], reverse=True)
    return {
        "origin": geo.point_dict(origin), "radius": radius_km, "units": units,
        "band": band, "mode": mode.upper(), "resolution": resolution,
        "window": fmt_duration(window_sec), "cells": out[:max_cells],
    }


def _cell_mode(by_band: dict, mode: str, at: int, ref_power: int) -> Optional[dict]:
    """Best band for one mode in a cell: (snr, power, time) lists keyed by band."""
    best = None
    for bn, lst in by_band.items():
        snrs = [s for s, _, _ in lst]
        times = [t for _, _, t in lst]
        median_snr = round(statistics.median(snrs), 1)
        conf = metrics.channel_confidence(len(lst), at - max(times), 0)
        obj = {"band": bn, "median_snr_db": median_snr,
               "confidence": conf, "observations": len(lst)}
        if mode == "wspr":
            powers = [p for _, p, _ in lst if p is not None]
            refs = [s + (ref_power - p) for s, p, _ in lst if p is not None]
            obj["ref_snr_db"] = round(statistics.median(refs), 1) if refs else None
            obj["median_power_dbm"] = round(statistics.median(powers)) if powers else None
            score = obj["ref_snr_db"] if obj["ref_snr_db"] is not None else -999
        else:
            obj["quality"] = metrics.quality(median_snr, "FT8")
            score = obj["quality"] * conf
        if best is None or score > best[0]:
            best = (score, obj)
    return best[1] if best else None


def _cell_rank(cells: list, requested: str) -> str:
    """Sort cells in place by the chosen mode's metric (fallback if absent)."""
    def has(mode: str) -> bool:
        return any(c[mode] for c in cells)

    if not has("ft8") and not has("wspr"):
        return requested
    basis = requested if has(requested) else ("wspr" if requested == "ft8" else "ft8")
    if basis == "ft8":
        cells.sort(reverse=True, key=lambda c:
                   c["ft8"]["quality"] * c["ft8"]["confidence"] if c["ft8"] else -1.0)
    else:
        cells.sort(reverse=True, key=lambda c:
                   c["wspr"]["ref_snr_db"] if c["wspr"] and c["wspr"]["ref_snr_db"] is not None
                   else -999)
    return basis


def coverage(conn, *, origin: Optional[str] = None, dest: Optional[str] = None,
             radius_km: float = DEFAULT_RADIUS_KM, units: str = "km",
             band: Optional[str] = None, resolution: str = "medium",
             window_sec: int = DEFAULT_COVERAGE_WINDOW_SEC, at: Optional[int] = None,
             max_cells: int = 2000, ref_power_dbm: int = DEFAULT_REF_POWER_DBM,
             rank: str = "ft8") -> dict:
    fixed = (origin or dest).upper()
    at = int(time.time()) if at is None else int(at)
    center = geo.grid_center(fixed)
    precision = geo.resolution_chars(resolution)
    rows = conn.execute(
        "SELECT band, mode, snr_db, power_dbm, observed_at, tx_grid, rx_grid "
        "FROM observations WHERE observed_at >= ? AND (tx_grid LIKE ? OR rx_grid LIKE ?)",
        (at - window_sec, fixed + "%", fixed + "%"),
    ).fetchall()

    # cells[cell][mode][band] = [(snr, power, time)]
    cells: dict = {}
    for r in rows:
        tx, rx = r["tx_grid"], r["rx_grid"]
        if tx.startswith(fixed) and not rx.startswith(fixed):
            other = rx
        elif rx.startswith(fixed) and not tx.startswith(fixed):
            other = tx
        else:
            continue
        if band and r["band"] != band:
            continue
        m = r["mode"].lower()
        if m not in _MODES:
            continue
        cell = other[:precision]
        clat, clon = geo.grid_center(cell)
        if geo.haversine_km(center[0], center[1], clat, clon) > radius_km:
            continue
        cells.setdefault(cell, {}).setdefault(m, {}).setdefault(r["band"], []).append(
            (r["snr_db"], r["power_dbm"], r["observed_at"]))

    out = []
    for cell, by_mode in cells.items():
        ft8 = _cell_mode(by_mode.get("ft8", {}), "ft8", at, ref_power_dbm)
        wspr = _cell_mode(by_mode.get("wspr", {}), "wspr", at, ref_power_dbm)
        if ft8 is None and wspr is None:
            continue
        clat, clon = geo.grid_center(cell)
        out.append({
            "grid": cell, "lat": clat, "lon": clon,
            "distance": geo.convert_km(geo.haversine_km(center[0], center[1], clat, clon), units),
            "bearing": geo.bearing(center[0], center[1], clat, clon),
            "ft8": ft8, "wspr": wspr,
        })

    basis = _cell_rank(out, rank)
    key = "origin" if origin else "dest"
    return {
        key: geo.point_dict(fixed), "radius": radius_km, "units": units,
        "resolution": resolution, "window": fmt_duration(window_sec),
        "ref_power_dbm": ref_power_dbm, "rank": basis, "cells": out[:max_cells],
    }
