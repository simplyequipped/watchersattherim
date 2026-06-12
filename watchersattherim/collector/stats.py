"""In-memory statistics snapshot.

Recomputed from the database on an interval; query handlers read the cached
snapshot rather than hitting the observations table. Ingest counters are
maintained incrementally on the write path.
"""

from __future__ import annotations

import time
from typing import Callable

from .ingest import IngestResult


class Stats:
    def __init__(self, conn, clock: Callable[[], float] = time.time):
        self.conn = conn
        self._clock = clock
        self.ingest = IngestResult()
        self.query_counts: dict[str, int] = {}
        self._snapshot: dict = {}

    def record_ingest(self, result: IngestResult) -> None:
        self.ingest.add(result)

    def record_query(self, source_hex: str) -> None:
        """Count a query from a source (for rate visibility / abuse triage)."""
        self.query_counts[source_hex] = self.query_counts.get(source_hex, 0) + 1

    def snapshot(self) -> dict:
        return self._snapshot

    def refresh(self, now: int | None = None) -> dict:
        now = int(self._clock()) if now is None else now
        c = self.conn
        h1, h24 = now - 3600, now - 86400

        def scalar(sql, params=()):
            return c.execute(sql, params).fetchone()[0]

        per_band = {
            r["band"]: r["n"] for r in c.execute(
                "SELECT band, COUNT(*) AS n FROM observations "
                "WHERE observed_at >= ? GROUP BY band ORDER BY n DESC", (h1,)
            ).fetchall()
        }

        self._snapshot = {
            "total_observations": scalar("SELECT COUNT(*) FROM observations"),
            "observations_1h": scalar("SELECT COUNT(*) FROM observations WHERE observed_at >= ?", (h1,)),
            "observations_24h": scalar("SELECT COUNT(*) FROM observations WHERE observed_at >= ?", (h24,)),
            "total_monitors": scalar("SELECT COUNT(*) FROM monitors"),
            "active_monitors": scalar("SELECT COUNT(*) FROM monitors WHERE last_seen >= ?", (h1,)),
            "per_band_1h": per_band,
            "distinct_tx_grids_24h": scalar(
                "SELECT COUNT(DISTINCT tx_grid) FROM observations WHERE observed_at >= ?", (h24,)),
            "distinct_rx_grids_24h": scalar(
                "SELECT COUNT(DISTINCT rx_grid) FROM observations WHERE observed_at >= ?", (h24,)),
            "ingest": {
                "accepted": self.ingest.accepted,
                "duplicates": self.ingest.duplicates,
                "rejected_allowlist": self.ingest.rejected_allowlist,
                "rejected_timestamp": self.ingest.rejected_timestamp,
                "rejected_schema": self.ingest.rejected_schema,
            },
            "queries_total": sum(self.query_counts.values()),
            "top_query_sources": sorted(
                self.query_counts.items(), key=lambda kv: kv[1], reverse=True
            )[:10],
            "refreshed_at": now,
        }
        return self._snapshot
