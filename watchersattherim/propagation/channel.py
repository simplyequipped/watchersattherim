"""channel and channel/anomaly: point-to-point nowcast estimates.

channel ranks the bands usable from origin to dest right now. channel/anomaly is
the same now-signal annotated with how it compares to the recent normal for this
hour. Both lean on reciprocity (we mostly observe the reverse path).
"""

from __future__ import annotations

import math
import statistics
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from ..common.config import fmt_duration
from . import geo, metrics

DEFAULT_WINDOW_SEC = 1800            # 30m nowcast
DEFAULT_BASELINE_SEC = 7 * 86400     # 7d rolling baseline for anomaly

_PRECISIONS = (6, 4, 2)
_WIDEN_THRESHOLD = 3


def _precisions(origin: str, dest: str) -> list[int]:
    cap = min(len(origin), len(dest))
    return [p for p in _PRECISIONS if p <= cap] or [cap]


def _candidate_bands(conn, origin: str, dest: str, since: int) -> list[str]:
    o, d = origin[:2], dest[:2]
    rows = conn.execute(
        "SELECT DISTINCT band FROM observations WHERE observed_at >= ? "
        "AND ((tx_grid LIKE ? AND rx_grid LIKE ?) OR (tx_grid LIKE ? AND rx_grid LIKE ?))",
        (since, o + "%", d + "%", d + "%", o + "%"),
    ).fetchall()
    return [r["band"] for r in rows]


def _match_rows(conn, o: str, d: str, band: str, since: int):
    return conn.execute(
        "SELECT snr_db, observed_at, tx_grid, rx_grid FROM observations "
        "WHERE band = ? AND observed_at >= ? "
        "AND ((tx_grid LIKE ? AND rx_grid LIKE ?) OR (tx_grid LIKE ? AND rx_grid LIKE ?))",
        (band, since, o + "%", d + "%", d + "%", o + "%"),
    ).fetchall()


def _band_estimate(conn, origin: str, dest: str, band: str, since: int, at: int,
                   widen: bool) -> Optional[dict]:
    precisions = _precisions(origin, dest)
    if not widen:
        precisions = precisions[:1]
    requested = precisions[0]
    for i, p in enumerate(precisions):
        o, d = origin[:p], dest[:p]
        rows = _match_rows(conn, o, d, band, since)
        last = i == len(precisions) - 1
        if len(rows) >= _WIDEN_THRESHOLD or (last and rows):
            return _summarize(rows, o, d, band, at, p, requested)
    return None


def _summarize(rows, o: str, d: str, band: str, at: int, precision: int,
               requested: int) -> dict:
    snrs = [r["snr_db"] for r in rows]
    times = [r["observed_at"] for r in rows]
    reciprocal = sum(
        1 for r in rows
        if r["tx_grid"].startswith(d) and r["rx_grid"].startswith(o)
        and not (r["tx_grid"].startswith(o) and r["rx_grid"].startswith(d))
    )
    median_snr = round(statistics.median(snrs), 1)
    levels = (requested - precision) // 2
    return {
        "band": band,
        "quality": metrics.quality(median_snr),
        "median_snr_db": median_snr,
        "confidence": metrics.channel_confidence(len(rows), at - max(times), levels),
        "evidence": {
            "observations": len(rows),
            "reciprocal": reciprocal,
            "last_seen": max(times),
            "match_precision": precision,
        },
    }


def estimate(conn, *, origin: str, dest: str, bands: Optional[list[str]] = None,
             window_sec: int = DEFAULT_WINDOW_SEC, at: Optional[int] = None,
             widen: bool = True, units: str = "km") -> dict:
    at = int(time.time()) if at is None else int(at)
    since = at - window_sec
    origin, dest = origin.upper(), dest.upper()
    band_list = bands if bands is not None else _candidate_bands(conn, origin, dest, since)
    found = [
        e for b in band_list
        if (e := _band_estimate(conn, origin, dest, b, since, at, widen)) is not None
    ]
    found.sort(key=lambda b: (b["quality"] * b["confidence"], b["evidence"]["observations"]),
               reverse=True)
    return {
        "origin": geo.point_dict(origin), "dest": geo.point_dict(dest),
        "distance": geo.convert_km(geo.grid_distance_km(origin, dest), units), "units": units,
        "bearing": geo.grid_bearing(origin, dest),
        "at": at, "window": fmt_duration(window_sec),
        "ranked": [b["band"] for b in found], "bands": found,
    }


# --- anomaly ----------------------------------------------------------------

def _hour_of(ts: int, tz: ZoneInfo) -> int:
    return datetime.fromtimestamp(ts, tz).hour


def _date_of(ts: int, tz: ZoneInfo):
    return datetime.fromtimestamp(ts, tz).date()


def _baseline(conn, origin: str, dest: str, band: str, since: int, target_hour: int,
              tz: ZoneInfo, total_days: int) -> Optional[dict]:
    rows = [r for r in _match_rows(conn, origin, dest, band, since)
            if _hour_of(r["observed_at"], tz) == target_hour]
    if not rows:
        return None
    days = {_date_of(r["observed_at"], tz) for r in rows}
    median_snr = round(statistics.median([r["snr_db"] for r in rows]), 1)
    return {
        "openness": round(min(1.0, len(days) / total_days), 2),
        "quality": metrics.quality(median_snr),
        "median_snr_db": median_snr,
        "observations": len(rows),
    }


def anomaly(conn, *, origin: str, dest: str, bands: Optional[list[str]] = None,
            window_sec: int = DEFAULT_WINDOW_SEC, baseline_sec: int = DEFAULT_BASELINE_SEC,
            at: Optional[int] = None, timezone: str = "UTC", widen: bool = True,
            units: str = "km") -> dict:
    at = int(time.time()) if at is None else int(at)
    origin, dest = origin.upper(), dest.upper()
    tz = ZoneInfo(timezone)
    since_cur = at - window_sec
    since_base = at - baseline_sec
    target_hour = _hour_of(at, tz)

    # Baseline denominator = days available, not the full window: before the window
    # has filled with real data, dividing by the whole window understates openness
    # and makes every current opening look spuriously "enhanced".
    window_days = max(1, round(baseline_sec / 86400))
    earliest = conn.execute(
        "SELECT MIN(observed_at) FROM observations WHERE observed_at >= ?", (since_base,)
    ).fetchone()[0]
    total_days = window_days if earliest is None \
        else min(window_days, max(1, math.ceil((at - earliest) / 86400)))

    # Candidate bands come from the baseline period, so a band that is normally
    # open but silent right now is still considered (the key depression signal).
    band_list = bands if bands is not None else _candidate_bands(conn, origin, dest, since_base)

    out = []
    for band in band_list:
        cur = _band_estimate(conn, origin, dest, band, since_cur, at, widen)
        base = _baseline(conn, origin, dest, band, since_base, target_hour, tz, total_days)
        if cur is None and base is None:
            continue
        cur_open = 1.0 if cur else 0.0
        base_open = base["openness"] if base else 0.0
        entry = {
            "band": band,
            "quality": cur["quality"] if cur else None,
            "median_snr_db": cur["median_snr_db"] if cur else None,
            "confidence": cur["confidence"] if cur else 0.0,
            "evidence": cur["evidence"] if cur else
            {"observations": 0, "reciprocal": 0, "last_seen": None, "match_precision": None},
            "baseline": base or
            {"openness": 0.0, "quality": None, "median_snr_db": None, "observations": 0},
            "deviation": round(cur_open - base_open, 2),
        }
        out.append(entry)

    out.sort(key=lambda b: abs(b["deviation"]), reverse=True)
    return {
        "origin": geo.point_dict(origin), "dest": geo.point_dict(dest),
        "distance": geo.convert_km(geo.grid_distance_km(origin, dest), units), "units": units,
        "bearing": geo.grid_bearing(origin, dest),
        "at": at, "window": fmt_duration(window_sec), "baseline": fmt_duration(baseline_sec),
        "bands": out,
    }
