"""Build monitor->collector telemetry batches.

Accumulates observations into a window and builds the telemetry body. The wire
codec (msgpack then gzip) lives in ``common.wire``, shared with the query path.
"""

from __future__ import annotations

from typing import Optional

from .observations import Observation, grid_to_latlon

TELEMETRY_VERSION = 1   # batch schema version, separate from the wire codec


def monitor_meta(grid: str, sw_version: str,
                 lat: Optional[float] = None,
                 lon: Optional[float] = None) -> dict:
    """Build the per-monitor metadata block. lat/lon default to grid center."""
    if lat is None or lon is None:
        glat, glon = grid_to_latlon(grid)
        lat = glat if lat is None else lat
        lon = glon if lon is None else lon
    return {"grid": grid, "lat": lat, "lon": lon, "sw_version": sw_version}


class TelemetryBatcher:
    """Accumulates observation rows and a decode counter for one send window."""

    def __init__(self, include_callsigns: bool = False):
        self.include_callsigns = include_callsigns
        self._obs: list[dict] = []
        self.decodes_seen = 0

    def __len__(self) -> int:
        return len(self._obs)

    def note_decode(self) -> None:
        """Count a decode seen this window (for monitor-local stats)."""
        self.decodes_seen += 1

    def _row(self, obs: Observation, ts: int, freq_hz: int,
             band: str, mode: str) -> dict:
        row = {
            "ts": int(ts),
            "mode": mode,
            "band": band,
            "freq": int(freq_hz),
            "tx_lat": obs.tx_lat, "tx_lon": obs.tx_lon, "tx_grid": obs.tx_grid,
            "rx_lat": obs.rx_lat, "rx_lon": obs.rx_lon, "rx_grid": obs.rx_grid,
            "snr": obs.snr_db,
            "type": obs.kind,
        }
        if self.include_callsigns:
            row["tx_call"] = obs.tx_call
            row["rx_call"] = obs.rx_call
        if obs.power_dbm is not None:
            row["power_dbm"] = obs.power_dbm
        return row

    def add(self, obs: Observation, *, ts: int, freq_hz: int,
            band: str, mode: str = "FT8") -> None:
        self._obs.append(self._row(obs, ts, freq_hz, band, mode))

    def take(self) -> list[dict]:
        """Return and clear the accumulated observation rows."""
        rows, self._obs = self._obs, []
        return rows

    def reset_counters(self) -> None:
        self.decodes_seen = 0


def build_batch(
    monitor: dict,
    window_start: int,
    window_end: int,
    observations: list[dict],
    *,
    decodes_seen: int = 0,
    obs_dropped_queue: int = 0,
    cache_size: int = 0,
) -> dict:
    """Assemble the telemetry body."""
    return {
        "v": TELEMETRY_VERSION,
        "monitor": monitor,
        "window": {"start": int(window_start), "end": int(window_end)},
        "stats": {
            "decodes_seen": decodes_seen,
            "obs_emitted": len(observations),
            "obs_dropped_queue": obs_dropped_queue,
            "cache_size": cache_size,
        },
        "observations": observations,
    }
