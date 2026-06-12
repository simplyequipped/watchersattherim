"""Local callsign -> grid cache.

Enables indirect observations and backfills TX grids for decodes that carry a
callsign but no grid. Bounded by ``max_entries`` (LRU eviction) and
``entry_ttl_sec`` (age-based eviction, keyed on when the grid was last observed,
so a stale entry expires before a station can move bands). Optional SQLite
persistence lets the cache survive a restart.

Cache is local to each monitor; there is no global/shared cache.

NOTE on hashed calls: ft8mon renders every hashed callsign as ``<...22>`` (the
"22" is the hash *width*, not its value), so distinct hashed calls are
indistinguishable in the text output. A hash->callsign resolution table is
therefore not feasible from ft8mon stdout; we only ever cache plaintext calls.
"""

from __future__ import annotations

import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

from .observations import grid_to_latlon

DEFAULT_MAX_ENTRIES = 10000
DEFAULT_TTL_SEC = 7200  # 2 hours


@dataclass
class CacheEntry:
    grid: str
    lat: float
    lon: float
    last_seen: float   # epoch seconds the grid was last observed
    count: int         # number of times observed


class CallsignCache:
    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        entry_ttl_sec: int = DEFAULT_TTL_SEC,
        clock: Callable[[], float] = time.time,
    ):
        self.max_entries = max_entries
        self.entry_ttl_sec = entry_ttl_sec
        self._clock = clock
        self._d: "OrderedDict[str, CacheEntry]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, callsign: str) -> bool:
        return self.lookup(callsign) is not None

    def update(self, callsign: str, grid: str, ts: Optional[float] = None) -> None:
        """Learn (or refresh) a callsign's grid. Bad grids are ignored."""
        ts = self._clock() if ts is None else ts
        try:
            lat, lon = grid_to_latlon(grid)
        except ValueError:
            return
        entry = self._d.get(callsign)
        if entry is None:
            self._d[callsign] = CacheEntry(grid, lat, lon, ts, 1)
        else:
            entry.grid, entry.lat, entry.lon = grid, lat, lon
            entry.last_seen = ts
            entry.count += 1
        self._d.move_to_end(callsign)
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)  # evict least-recently-updated

    def lookup(self, callsign: str, ts: Optional[float] = None) -> Optional[str]:
        """Return the cached grid, or ``None`` if absent or expired."""
        entry = self._d.get(callsign)
        if entry is None:
            return None
        ts = self._clock() if ts is None else ts
        if ts - entry.last_seen > self.entry_ttl_sec:
            del self._d[callsign]
            return None
        return entry.grid

    def entry(self, callsign: str) -> Optional[CacheEntry]:
        return self._d.get(callsign)

    def prune(self, ts: Optional[float] = None) -> int:
        """Drop all expired entries; return how many were removed."""
        ts = self._clock() if ts is None else ts
        dead = [c for c, e in self._d.items() if ts - e.last_seen > self.entry_ttl_sec]
        for c in dead:
            del self._d[c]
        return len(dead)

    # --- persistence -------------------------------------------------------

    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS cache ("
        "callsign TEXT PRIMARY KEY, grid TEXT, lat REAL, lon REAL, "
        "last_seen REAL, count INTEGER)"
    )

    def save(self, path: str) -> None:
        con = sqlite3.connect(path)
        try:
            con.execute(self._SCHEMA)
            con.execute("DELETE FROM cache")
            con.executemany(
                "INSERT INTO cache VALUES (?,?,?,?,?,?)",
                [(c, e.grid, e.lat, e.lon, e.last_seen, e.count)
                 for c, e in self._d.items()],
            )
            con.commit()
        finally:
            con.close()

    def load(self, path: str) -> None:
        con = sqlite3.connect(path)
        try:
            con.execute(self._SCHEMA)
            rows = con.execute(
                "SELECT callsign, grid, lat, lon, last_seen, count "
                "FROM cache ORDER BY last_seen"
            ).fetchall()
        finally:
            con.close()
        for c, grid, lat, lon, last_seen, count in rows:
            self._d[c] = CacheEntry(grid, lat, lon, last_seen, count)
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)
