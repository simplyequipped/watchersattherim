"""SQLite storage for the collector.

Raw SQL, single file, no ORM. One row per propagation observation; a registry of
known monitor nodes. Insertion is idempotent via a unique index so a monitor
retrying a batch produces no duplicate rows.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitors (
    address     BLOB PRIMARY KEY,
    grid        TEXT,
    lat         REAL,
    lon         REAL,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    sw_version  TEXT,
    allowed     INTEGER NOT NULL DEFAULT 1,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor           BLOB NOT NULL,
    observed_at       INTEGER NOT NULL,
    mode              TEXT NOT NULL,
    band              TEXT NOT NULL,
    freq_hz           INTEGER NOT NULL,
    tx_lat            REAL NOT NULL,
    tx_lon            REAL NOT NULL,
    tx_grid           TEXT NOT NULL,
    rx_lat            REAL NOT NULL,
    rx_lon            REAL NOT NULL,
    rx_grid           TEXT NOT NULL,
    snr_db            INTEGER NOT NULL,
    observation_type  TEXT NOT NULL,
    tx_call           TEXT,
    rx_call           TEXT,
    power_dbm         INTEGER,
    FOREIGN KEY (monitor) REFERENCES monitors(address)
);

CREATE INDEX IF NOT EXISTS idx_obs_observed     ON observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_tx_grid_time ON observations(tx_grid, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_rx_grid_time ON observations(rx_grid, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_band_time    ON observations(band,    observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_monitor_time ON observations(monitor, observed_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_dedup
    ON observations(monitor, observed_at, freq_hz, tx_grid);

CREATE TABLE IF NOT EXISTS query_blocks (
    address     BLOB PRIMARY KEY,
    blocked_at  INTEGER NOT NULL
);
"""

# Observation columns populated from a telemetry row, in insertion order.
_OBS_COLUMNS = (
    "monitor", "observed_at", "mode", "band", "freq_hz",
    "tx_lat", "tx_lon", "tx_grid", "rx_lat", "rx_lon", "rx_grid",
    "snr_db", "observation_type", "tx_call", "rx_call", "power_dbm",
)


def connect(path: str) -> sqlite3.Connection:
    # check_same_thread=False: the HTTP server thread and the LXMF/ingest thread
    # share one connection, serialized by a lock in the collector main loop.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# --- monitors -------------------------------------------------------------

def upsert_monitor(conn, address: bytes, *, grid=None, lat=None, lon=None,
                   sw_version=None, now: int) -> None:
    """Record/refresh a monitor. Preserves first_seen and the allowed flag."""
    conn.execute(
        """
        INSERT INTO monitors (address, grid, lat, lon, first_seen, last_seen, sw_version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            grid=excluded.grid, lat=excluded.lat, lon=excluded.lon,
            last_seen=excluded.last_seen, sw_version=excluded.sw_version
        """,
        (address, grid, lat, lon, now, now, sw_version),
    )
    conn.commit()


def list_monitors(conn):
    """All known monitors, most-recently-seen first."""
    return conn.execute(
        "SELECT address, grid, lat, lon, last_seen, sw_version, allowed "
        "FROM monitors ORDER BY last_seen DESC"
    ).fetchall()


def is_allowed(conn, address: bytes) -> bool:
    row = conn.execute(
        "SELECT allowed FROM monitors WHERE address = ?", (address,)
    ).fetchone()
    return bool(row and row["allowed"])


def set_allowed(conn, address: bytes, allowed: bool, *, now: int,
                notes: Optional[str] = None) -> None:
    """Add or update an allowlist entry, creating the monitor row if needed."""
    conn.execute(
        """
        INSERT INTO monitors (address, first_seen, last_seen, allowed, notes)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET allowed=excluded.allowed, notes=excluded.notes
        """,
        (address, now, now, 1 if allowed else 0, notes),
    )
    conn.commit()


# --- query access (deny abusive requesters) -------------------------------

def set_query_blocked(conn, address: bytes, blocked: bool, *, now: int) -> None:
    if blocked:
        conn.execute("INSERT OR REPLACE INTO query_blocks VALUES (?, ?)", (address, now))
    else:
        conn.execute("DELETE FROM query_blocks WHERE address = ?", (address,))
    conn.commit()


def is_query_blocked(conn, address: bytes) -> bool:
    return conn.execute(
        "SELECT 1 FROM query_blocks WHERE address = ?", (address,)
    ).fetchone() is not None


def list_query_blocks(conn):
    return conn.execute(
        "SELECT address, blocked_at FROM query_blocks ORDER BY blocked_at DESC"
    ).fetchall()


# --- observations ---------------------------------------------------------

def insert_observations(conn, monitor: bytes, rows: Iterable[dict]) -> int:
    """Insert telemetry observation rows; returns the number actually stored.

    Uses INSERT OR IGNORE against the unique dedup index, so retried batches and
    duplicate rows are silently skipped.
    """
    inserted = 0
    cur = conn.cursor()
    for r in rows:
        values = (
            monitor, r["ts"], r["mode"], r["band"], r["freq"],
            r["tx_lat"], r["tx_lon"], r["tx_grid"],
            r["rx_lat"], r["rx_lon"], r["rx_grid"],
            r["snr"], r["type"], r.get("tx_call"), r.get("rx_call"),
            r.get("power_dbm"),
        )
        cur.execute(
            f"INSERT OR IGNORE INTO observations ({', '.join(_OBS_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_OBS_COLUMNS))})",
            values,
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def count_observations(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM observations").fetchone()["n"]


# --- retention ------------------------------------------------------------

def prune(conn, retention_days: int, *, now: int) -> int:
    """Delete observations older than the retention window. Returns rows deleted."""
    cutoff = now - retention_days * 86400
    cur = conn.execute("DELETE FROM observations WHERE observed_at < ?", (cutoff,))
    conn.commit()
    deleted = cur.rowcount
    conn.execute("VACUUM")
    return deleted
