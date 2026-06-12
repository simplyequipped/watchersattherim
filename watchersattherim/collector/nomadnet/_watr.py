"""Shared helpers for the watchersattherim NomadNet pages (stdlib only).

These run as NomadNet dynamic pages (each .mu is an executable that prints micron
to stdout) and query the collector's SQLite database directly.

NomadNet passes request data in the environment: form fields as ``field_<name>``
and link variables as ``var_<name>`` (see NomadNet Node.py). It does not pass
arbitrary env vars, so set the database location by editing DB_PATH below if your
collector does not use the default.
"""

import os
import sqlite3
import statistics
import time

DB_PATH = os.environ.get("WATR_COLLECTOR_DB") or os.path.expanduser(
    "~/.watchersattherim/collector/collector.db"
)

NAV = (
    "`[Home`:/page/index.mu] "
    "`[Path`:/page/path.mu] "
    "`[From`:/page/from.mu] "
    "`[To`:/page/to.mu] "
    "`[Band`:/page/band.mu] "
    "`[Monitors`:/page/monitors.mu] "
    "`[About`:/page/about.mu]"
)


# --- request + formatting helpers -----------------------------------------

def request_var(name, default=None):
    return os.environ.get("field_" + name) or os.environ.get("var_" + name) or default


def as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def header(title):
    print("`c`!" + title + "`!`a")
    print("-")
    print(NAV)
    print("-")
    print("")


def hhmmss(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


def render(result):
    """Render a query result (observations + summary) as micron."""
    summary = result["summary"]
    if summary["count"] == 0:
        print("`F900No observations found.`f")
        return
    print(">Summary")
    print(f"  count {summary['count']}   monitors {summary['monitor_count']}   "
          f"direct {summary['direct_count']}   indirect {summary['indirect_count']}")
    print(f"  SNR  min {summary['snr_min']}   median {summary['snr_median']}   "
          f"max {summary['snr_max']}")
    print("")
    print(">Observations")
    print("`!  TX     RX     SNR  band  freq (Hz)   observed (UTC)`!")
    for o in result["observations"][:100]:
        print("  {:6} {:6} {:>3}  {:4}  {:>9}  {}".format(
            o["tx_grid"], o["rx_grid"], o["snr_db"], o["band"],
            o["freq_hz"], hhmmss(o["observed_at"])))


# --- database access ------------------------------------------------------

def connect():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _since(hours):
    return int(time.time()) - hours * 3600


def _result(rows):
    if not rows:
        return {"observations": [], "summary": {"count": 0}}
    snrs = [r["snr_db"] for r in rows]
    monitors = {r["monitor"] for r in rows}
    direct = sum(1 for r in rows if r["observation_type"] == "direct")
    return {
        "observations": [dict(r) for r in rows],
        "summary": {
            "count": len(rows),
            "snr_min": min(snrs), "snr_max": max(snrs),
            "snr_median": round(statistics.median(snrs), 1),
            "monitor_count": len(monitors),
            "direct_count": direct, "indirect_count": len(rows) - direct,
        },
    }


def _select(where, params):
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM observations WHERE {where} ORDER BY observed_at", params,
        ).fetchall()
    return _result(rows)


def q_path(tx_grid, rx_grid, hours):
    return _select(
        "tx_grid LIKE ? AND rx_grid LIKE ? AND observed_at >= ?",
        (tx_grid.upper() + "%", rx_grid.upper() + "%", _since(hours)),
    )


def q_from(grid, hours):
    return _select("tx_grid LIKE ? AND observed_at >= ?",
                   (grid.upper() + "%", _since(hours)))


def q_to(grid, hours):
    return _select("rx_grid LIKE ? AND observed_at >= ?",
                   (grid.upper() + "%", _since(hours)))


def q_band(band, hours):
    return _select("band = ? AND observed_at >= ?", (band, _since(hours)))


def q_stats():
    now = int(time.time())
    with connect() as conn:
        def scalar(sql, params=()):
            return conn.execute(sql, params).fetchone()[0]
        return {
            "total": scalar("SELECT COUNT(*) FROM observations"),
            "obs_24h": scalar("SELECT COUNT(*) FROM observations WHERE observed_at >= ?",
                              (now - 86400,)),
            "active_monitors": scalar(
                "SELECT COUNT(*) FROM monitors WHERE last_seen >= ?", (now - 3600,)),
            "total_monitors": scalar("SELECT COUNT(*) FROM monitors"),
        }


def q_monitors():
    now = int(time.time())
    with connect() as conn:
        return conn.execute(
            "SELECT address, grid, last_seen FROM monitors "
            "WHERE last_seen >= ? ORDER BY last_seen DESC", (now - 86400,),
        ).fetchall()
