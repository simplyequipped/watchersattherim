"""trend/path/* and trend/band/*: temporal patterns over time.

trend/path/{hour,month,year} is a path's recurring pattern (openness curve);
trend/path/anomaly is a chronological hourly deviation series for event detection;
trend/band/{hour,month,year} is band-wide activity over time. Buckets are computed
in the requested timezone.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from . import geo, metrics

UNITS = ("hour", "month", "year")
SLOTS_PER_HOUR = 240            # 3600s / 15s FT8 slot
DEFAULT_ANOMALY_WINDOW_SEC = 7 * 86400


def _bucket(dt: datetime, unit: str) -> int:
    return {"hour": dt.hour, "month": dt.month, "year": dt.year}[unit]


def _passes(dt: datetime, filters: dict, axis: str) -> bool:
    for key, val in filters.items():
        if key == axis:
            continue
        if key == "hour" and dt.hour != val:
            return False
        if key == "month" and dt.month != val:
            return False
        if key == "year" and dt.year != val:
            return False
    return True


def _dates_in_period(dates: set, bucket: int, unit: str) -> int:
    if unit == "hour":
        return len(dates) or 1
    if unit == "month":
        return len({d for d in dates if d.month == bucket}) or 1
    return len({d for d in dates if d.year == bucket}) or 1


def _path_rows(conn, origin: str, dest: str, since: int):
    return conn.execute(
        "SELECT band, snr_db, observed_at FROM observations WHERE observed_at >= ? "
        "AND ((tx_grid LIKE ? AND rx_grid LIKE ?) OR (tx_grid LIKE ? AND rx_grid LIKE ?))",
        (since, origin + "%", dest + "%", dest + "%", origin + "%"),
    ).fetchall()


def path(conn, *, origin: str, dest: str, unit: str, bands: Optional[list[str]] = None,
         filters: Optional[dict] = None, timezone: str = "UTC",
         max_window_sec: int = 90 * 86400, now: Optional[int] = None,
         units: str = "km") -> dict:
    origin, dest = origin.upper(), dest.upper()
    tz = ZoneInfo(timezone)
    filters = filters or {}
    now = int(time.time()) if now is None else now
    since = now - max_window_sec
    rows = _path_rows(conn, origin, dest, since)

    by_band: dict[str, list] = {}
    for r in rows:
        if bands is not None and r["band"] not in bands:
            continue
        dt = datetime.fromtimestamp(r["observed_at"], tz)
        if not _passes(dt, filters, unit):
            continue
        by_band.setdefault(r["band"], []).append((dt, r["observed_at"], r["snr_db"]))

    result = {b: {"items": _path_curve(items, unit)} for b, items in by_band.items()}
    return {
        "origin": geo.point_dict(origin), "dest": geo.point_dict(dest),
        "distance": geo.convert_km(geo.grid_distance_km(origin, dest), units), "units": units,
        "bearing": geo.grid_bearing(origin, dest),
        "unit": unit, "filters": filters, "bands": result,
    }


def _path_curve(items: list, unit: str) -> list[dict]:
    all_dates = {dt.date() for dt, _, _ in items}
    buckets: dict[int, list] = {}
    for dt, slot, snr in items:
        buckets.setdefault(_bucket(dt, unit), []).append((dt, slot, snr))

    out = []
    for b in sorted(buckets):
        bitems = buckets[b]
        snrs = [s for _, _, s in bitems]
        decoded_slots = len({slot for _, slot, _ in bitems})
        possible = SLOTS_PER_HOUR * _dates_in_period(all_dates, b, unit)
        median_snr = round(statistics.median(snrs), 1)
        out.append({
            unit: b,
            "openness": round(min(1.0, decoded_slots / possible), 2),
            "quality": metrics.quality(median_snr),
            "median_snr_db": median_snr,
            "confidence": metrics.historical_confidence(len(bitems)),
            "observations": len(bitems),
        })
    return out


# --- trend/path/anomaly -----------------------------------------------------

def path_anomaly(conn, *, origin: str, dest: str, bands: Optional[list[str]] = None,
                 window_sec: int = DEFAULT_ANOMALY_WINDOW_SEC,
                 start: Optional[int] = None, end: Optional[int] = None,
                 timezone: str = "UTC") -> dict:
    origin, dest = origin.upper(), dest.upper()
    tz = ZoneInfo(timezone)
    end = int(time.time()) if end is None else int(end)
    start = end - window_sec if start is None else int(start)
    rows = _path_rows(conn, origin, dest, start)
    rows = [r for r in rows if r["observed_at"] <= end]

    by_band: dict[str, list] = {}
    for r in rows:
        if bands is not None and r["band"] not in bands:
            continue
        by_band.setdefault(r["band"], []).append((r["observed_at"], r["snr_db"]))

    hour0 = (start // 3600) * 3600
    hourN = (end // 3600) * 3600
    hours = list(range(hour0, hourN + 3600, 3600))

    result = {}
    for band, obs in by_band.items():
        result[band] = {"items": _anomaly_series(obs, hours, tz)}
    return {
        "origin": geo.point_dict(origin), "dest": geo.point_dict(dest),
        "unit": "hour", "window": _fmt(window_sec) if start is None else None,
        "start": start, "end": end, "bands": result,
    }


def _anomaly_series(obs: list, hours: list, tz: ZoneInfo) -> list[dict]:
    # openness per actual hour = distinct decoded slots / 240
    by_hour: dict[int, set] = {}
    for slot, _snr in obs:
        hf = (slot // 3600) * 3600
        by_hour.setdefault(hf, set()).add(slot)
    openness = {hf: round(min(1.0, len(slots) / SLOTS_PER_HOUR), 2)
                for hf, slots in by_hour.items()}

    # per-hour-of-day median baseline across the window
    hod: dict[int, list] = {}
    for hf in hours:
        h = datetime.fromtimestamp(hf, tz).hour
        hod.setdefault(h, []).append(openness.get(hf, 0.0))
    baseline = {h: round(statistics.median(vals), 2) for h, vals in hod.items()}

    items = []
    for hf in hours:
        h = datetime.fromtimestamp(hf, tz).hour
        o = openness.get(hf, 0.0)
        items.append({
            "time": hf,
            "openness": o,
            "baseline": baseline[h],
            "deviation": round(o - baseline[h], 2),
            "observations": len(by_hour.get(hf, ())),
        })
    return items


# --- trend/band/* -----------------------------------------------------------

def band(conn, *, band: str, unit: str, origin: Optional[str] = None,
         radius_km: Optional[float] = None, filters: Optional[dict] = None,
         timezone: str = "UTC", max_window_sec: int = 90 * 86400,
         now: Optional[int] = None, units: str = "km") -> dict:
    tz = ZoneInfo(timezone)
    filters = filters or {}
    now = int(time.time()) if now is None else now
    since = now - max_window_sec
    rows = conn.execute(
        "SELECT snr_db, observed_at, tx_grid, rx_grid FROM observations "
        "WHERE band = ? AND observed_at >= ?",
        (band, since),
    ).fetchall()

    centers: dict[str, tuple] = {}

    def center_of(grid):
        if grid not in centers:
            centers[grid] = geo.grid_center(grid)
        return centers[grid]

    center = None
    region = None
    if origin is not None and radius_km is not None:
        origin = origin.upper()
        center = geo.grid_center(origin)
        region = {"origin": geo.point_dict(origin), "radius_km": round(radius_km, 1)}

    buckets: dict[int, list] = {}
    for r in rows:
        dt = datetime.fromtimestamp(r["observed_at"], tz)
        if not _passes(dt, filters, unit):
            continue
        tlat, tlon = center_of(r["tx_grid"])
        if center is not None and geo.haversine_km(center[0], center[1], tlat, tlon) > radius_km:
            continue
        rlat, rlon = center_of(r["rx_grid"])
        path_km = geo.haversine_km(tlat, tlon, rlat, rlon)   # length of the observed path
        buckets.setdefault(_bucket(dt, unit), []).append((r["snr_db"], r["tx_grid"], path_km))

    items = []
    for b in sorted(buckets):
        bitems = buckets[b]
        snrs = [s for s, _, _ in bitems]
        median_snr = round(statistics.median(snrs), 1)
        items.append({
            unit: b,
            "observations": len(bitems),
            "grids": len({g for _, g, _ in bitems}),
            "distance": geo.convert_km(statistics.median([d for _, _, d in bitems]), units),
            "quality": metrics.quality(median_snr),
            "median_snr_db": median_snr,
            "confidence": metrics.historical_confidence(len(bitems)),
        })
    return {"band": band, "unit": unit, "units": units, "region": region,
            "filters": filters, "items": items}


def _fmt(sec: int) -> str:
    from ..common.config import fmt_duration
    return fmt_duration(sec)
