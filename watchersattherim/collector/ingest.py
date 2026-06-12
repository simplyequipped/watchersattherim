"""Ingest validation and insertion.

A batch from a monitor is checked before insertion: source authorization,
per-observation timestamp sanity, schema validation, then dedup insert. Counts
of accepted/rejected/duplicate rows are returned for the stats path.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import storage

_REQUIRED_FIELDS = (
    "ts", "mode", "band", "freq",
    "tx_lat", "tx_lon", "tx_grid", "rx_lat", "rx_lon", "rx_grid",
    "snr", "type",
)
SNR_MIN, SNR_MAX = -50, 30


@dataclass
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    rejected_allowlist: int = 0
    rejected_timestamp: int = 0
    rejected_schema: int = 0

    def add(self, other: "IngestResult") -> None:
        self.accepted += other.accepted
        self.duplicates += other.duplicates
        self.rejected_allowlist += other.rejected_allowlist
        self.rejected_timestamp += other.rejected_timestamp
        self.rejected_schema += other.rejected_schema


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _valid_schema(obs: dict) -> bool:
    if not all(k in obs for k in _REQUIRED_FIELDS):
        return False
    if obs["type"] not in ("direct", "indirect"):
        return False
    if not (_num(obs["tx_lat"]) and -90 <= obs["tx_lat"] <= 90):
        return False
    if not (_num(obs["rx_lat"]) and -90 <= obs["rx_lat"] <= 90):
        return False
    if not (_num(obs["tx_lon"]) and -180 <= obs["tx_lon"] <= 180):
        return False
    if not (_num(obs["rx_lon"]) and -180 <= obs["rx_lon"] <= 180):
        return False
    if not (_num(obs["snr"]) and SNR_MIN <= obs["snr"] <= SNR_MAX):
        return False
    if not _num(obs["freq"]) or not _num(obs["ts"]):
        return False
    return True


def _valid_timestamp(ts, *, now: int, older_than: int, future_skew: int) -> bool:
    return (now - older_than) <= ts <= (now + future_skew)


def ingest_batch(conn, source: bytes, batch: dict, *, config, now: int) -> IngestResult:
    """Validate and insert one telemetry batch from ``source``."""
    result = IngestResult()
    observations = batch.get("observations", []) if isinstance(batch, dict) else []

    # 1. source authorization
    if config.allowlist_mode != "open" and not storage.is_allowed(conn, source):
        result.rejected_allowlist = len(observations)
        return result

    # register/refresh the monitor
    meta = batch.get("monitor", {}) if isinstance(batch, dict) else {}
    storage.upsert_monitor(
        conn, source,
        grid=meta.get("grid"), lat=meta.get("lat"), lon=meta.get("lon"),
        sw_version=meta.get("sw_version"), now=now,
    )

    # 2 + 3. per-observation timestamp + schema validation
    good = []
    for obs in observations:
        if not isinstance(obs, dict) or not _valid_schema(obs):
            result.rejected_schema += 1
            continue
        if not _valid_timestamp(
            obs["ts"], now=now,
            older_than=config.reject_older_than_sec,
            future_skew=config.reject_future_skew_sec,
        ):
            result.rejected_timestamp += 1
            continue
        good.append(obs)

    # 4. dedup insert
    inserted = storage.insert_observations(conn, source, good)
    result.accepted = inserted
    result.duplicates = len(good) - inserted
    return result
