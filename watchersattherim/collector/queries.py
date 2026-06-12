"""v1 query commands over the observations store.

Each command returns a ``result`` shape - a list of observations plus a summary.
The LXMF and HTTP layers wrap these in their own envelopes.
"""

from __future__ import annotations

import statistics
from typing import Optional

DEFAULT_HOURS = 4
MAX_OBSERVATIONS = 1000


def _obs_dict(row) -> dict:
    return {
        "monitor": row["monitor"].hex(),
        "observed_at": row["observed_at"],
        "mode": row["mode"],
        "band": row["band"],
        "freq_hz": row["freq_hz"],
        "tx_grid": row["tx_grid"], "tx_lat": row["tx_lat"], "tx_lon": row["tx_lon"],
        "rx_grid": row["rx_grid"], "rx_lat": row["rx_lat"], "rx_lon": row["rx_lon"],
        "snr_db": row["snr_db"],
        "type": row["observation_type"],
    }


def _select(conn, where: str, params: tuple):
    return conn.execute(
        f"SELECT * FROM observations WHERE {where} ORDER BY observed_at", params
    ).fetchall()


def _result(rows) -> dict:
    if not rows:
        return {"observations": [], "summary": {"count": 0}}
    snrs = [r["snr_db"] for r in rows]
    times = [r["observed_at"] for r in rows]
    monitors = {r["monitor"] for r in rows}
    direct = sum(1 for r in rows if r["observation_type"] == "direct")
    summary = {
        "count": len(rows),
        "snr_min": min(snrs), "snr_max": max(snrs),
        "snr_median": round(statistics.median(snrs), 1),
        "first_seen": min(times), "last_seen": max(times),
        "monitor_count": len(monitors),
        "direct_count": direct, "indirect_count": len(rows) - direct,
    }
    return {"observations": [_obs_dict(r) for r in rows[:MAX_OBSERVATIONS]], "summary": summary}


def _since(now: int, hours: int) -> int:
    return now - hours * 3600


def path_query(conn, *, tx_grid: str, rx_grid: str, now: int,
               hours: int = DEFAULT_HOURS, band: Optional[str] = None) -> dict:
    where = "tx_grid LIKE ? AND rx_grid LIKE ? AND observed_at >= ?"
    params = [tx_grid.upper() + "%", rx_grid.upper() + "%", _since(now, hours)]
    if band:
        where += " AND band = ?"
        params.append(band)
    return _result(_select(conn, where, tuple(params)))


def from_grid(conn, *, grid: str, now: int, hours: int = DEFAULT_HOURS) -> dict:
    return _result(_select(
        conn, "tx_grid LIKE ? AND observed_at >= ?",
        (grid.upper() + "%", _since(now, hours)),
    ))


def to_grid(conn, *, grid: str, now: int, hours: int = DEFAULT_HOURS) -> dict:
    return _result(_select(
        conn, "rx_grid LIKE ? AND observed_at >= ?",
        (grid.upper() + "%", _since(now, hours)),
    ))


def band_activity(conn, *, band: str, now: int, hours: int = 1) -> dict:
    return _result(_select(
        conn, "band = ? AND observed_at >= ?", (band, _since(now, hours)),
    ))


def monitor_list(conn, *, now: int, active_within_sec: int = 3600) -> dict:
    rows = conn.execute(
        "SELECT address, grid, lat, lon, last_seen, sw_version, allowed "
        "FROM monitors WHERE last_seen >= ? ORDER BY last_seen DESC",
        (now - active_within_sec,),
    ).fetchall()
    return {"monitors": [{
        "address": r["address"].hex(), "grid": r["grid"],
        "lat": r["lat"], "lon": r["lon"], "last_seen": r["last_seen"],
        "sw_version": r["sw_version"], "allowed": bool(r["allowed"]),
    } for r in rows]}


def monitor_info(conn, *, address: str, now: int) -> dict:
    addr = bytes.fromhex(address)
    row = conn.execute("SELECT * FROM monitors WHERE address = ?", (addr,)).fetchone()
    if row is None:
        return {"monitor": None}
    obs_24h = conn.execute(
        "SELECT COUNT(*) AS n FROM observations WHERE monitor = ? AND observed_at >= ?",
        (addr, now - 86400),
    ).fetchone()["n"]
    return {"monitor": {
        "address": row["address"].hex(), "grid": row["grid"],
        "lat": row["lat"], "lon": row["lon"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "sw_version": row["sw_version"], "allowed": bool(row["allowed"]),
        "observations_24h": obs_24h,
    }}
