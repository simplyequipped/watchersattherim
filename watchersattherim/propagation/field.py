"""map and coverage: per-cell fields over an area.

map is band activity over space (origin-agnostic, aggregated). coverage is the
channel estimate run per cell from (or to) a fixed endpoint. Both return only
cells that have observations; there is no interpolation into empty cells.
"""

from __future__ import annotations

import statistics
import time
from typing import Optional

from ..common.config import fmt_duration
from . import geo, metrics

DEFAULT_MAP_WINDOW_SEC = 3600
DEFAULT_COVERAGE_WINDOW_SEC = 1800
DEFAULT_RADIUS_KM = 2000


def map_field(conn, *, origin: str, radius_km: float = DEFAULT_RADIUS_KM,
              units: str = "km", band: str = "40m", resolution: str = "medium",
              window_sec: int = DEFAULT_MAP_WINDOW_SEC, at: Optional[int] = None,
              max_cells: int = 2000) -> dict:
    origin = origin.upper()
    at = int(time.time()) if at is None else int(at)
    center = geo.grid_center(origin)
    precision = geo.resolution_chars(resolution)
    rows = conn.execute(
        "SELECT snr_db, tx_grid FROM observations WHERE band = ? AND observed_at >= ?",
        (band, at - window_sec),
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
            "quality": metrics.quality(median_snr),
            "median_snr_db": median_snr,
            "observations": len(snrs),
        })
    out.sort(key=lambda c: c["observations"], reverse=True)
    return {
        "origin": geo.point_dict(origin), "radius": radius_km, "units": units,
        "band": band, "resolution": resolution, "window": fmt_duration(window_sec),
        "cells": out[:max_cells],
    }


def coverage(conn, *, origin: Optional[str] = None, dest: Optional[str] = None,
             radius_km: float = DEFAULT_RADIUS_KM, units: str = "km",
             band: Optional[str] = None, resolution: str = "medium",
             window_sec: int = DEFAULT_COVERAGE_WINDOW_SEC, at: Optional[int] = None,
             max_cells: int = 2000) -> dict:
    fixed = (origin or dest).upper()
    at = int(time.time()) if at is None else int(at)
    center = geo.grid_center(fixed)
    precision = geo.resolution_chars(resolution)
    rows = conn.execute(
        "SELECT band, snr_db, observed_at, tx_grid, rx_grid FROM observations "
        "WHERE observed_at >= ? AND (tx_grid LIKE ? OR rx_grid LIKE ?)",
        (at - window_sec, fixed + "%", fixed + "%"),
    ).fetchall()

    cells: dict[str, dict[str, list]] = {}
    for r in rows:
        tx, rx = r["tx_grid"], r["rx_grid"]
        if tx.startswith(fixed) and not rx.startswith(fixed):
            other = rx
        elif rx.startswith(fixed) and not tx.startswith(fixed):
            other = tx
        else:
            continue
        cell = other[:precision]
        clat, clon = geo.grid_center(cell)
        if geo.haversine_km(center[0], center[1], clat, clon) > radius_km:
            continue
        cells.setdefault(cell, {}).setdefault(r["band"], []).append(
            (r["snr_db"], r["observed_at"]))

    out = []
    for cell, by_band in cells.items():
        candidates = ([(band, by_band[band])] if band in by_band else []) if band \
            else list(by_band.items())
        best = None
        for bn, lst in candidates:
            snrs = [s for s, _ in lst]
            times = [t for _, t in lst]
            median_snr = round(statistics.median(snrs), 1)
            q = metrics.quality(median_snr)
            c = metrics.channel_confidence(len(lst), at - max(times), 0)
            score = q * c
            if best is None or score > best[0]:
                best = (score, bn, q, median_snr, c, len(lst))
        if best is None:
            continue
        _score, bn, q, median_snr, c, n = best
        clat, clon = geo.grid_center(cell)
        out.append({
            "grid": cell, "lat": clat, "lon": clon, "band": bn,
            "distance": geo.convert_km(geo.haversine_km(center[0], center[1], clat, clon), units),
            "bearing": geo.bearing(center[0], center[1], clat, clon),
            "quality": q, "median_snr_db": median_snr,
            "confidence": c, "observations": n,
        })
    out.sort(key=lambda c: c["quality"], reverse=True)

    key = "origin" if origin else "dest"
    return {
        key: geo.point_dict(fixed), "radius": radius_km, "units": units,
        "resolution": resolution, "window": fmt_duration(window_sec),
        "cells": out[:max_cells],
    }
