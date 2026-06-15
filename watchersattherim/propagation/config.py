"""Configuration for the propagation layer (the collector's [propagation] section)."""

from __future__ import annotations

from dataclasses import dataclass

from ..common.config import parse_duration


@dataclass
class PropagationConfig:
    enabled: bool = True
    default_timezone: str = "UTC"
    # Resource caps protecting the shared DB connection from expensive queries.
    max_radius_km: int = 5000
    max_cells: int = 2000
    max_window_sec: int = 90 * 86400


def from_parser(cp) -> PropagationConfig:
    """Read a PropagationConfig from a configparser, all keys optional."""
    return PropagationConfig(
        enabled=cp.getboolean("propagation", "enabled", fallback=True),
        default_timezone=cp.get("propagation", "default_timezone", fallback="UTC"),
        max_radius_km=cp.getint("propagation", "max_radius_km", fallback=5000),
        max_cells=cp.getint("propagation", "max_cells", fallback=2000),
        max_window_sec=parse_duration(cp.get("propagation", "max_window", fallback="90d")),
    )
