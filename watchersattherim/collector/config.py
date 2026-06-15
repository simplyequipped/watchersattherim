"""Collector INI configuration.

All keys are optional - a bare config runs a local collector with defaults. The
allowlist seeds the monitors table at startup; in ``explicit`` mode only listed
(or DB-allowed) senders may write, in ``open`` mode anyone may.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from ..common.config import ConfigError, parse_duration, parser
from ..propagation.config import PropagationConfig
from ..propagation import config as propagation_config


@dataclass
class CollectorConfig:
    storage_dir: str = "~/.watchersattherim/collector"
    database: Optional[str] = None            # default: <storage_dir>/collector.db
    bind: str = "0.0.0.0"
    http_port: int = 8080
    http_api: bool = False
    allowlist_mode: str = "explicit"          # explicit | open
    allowed: tuple[str, ...] = ()             # hex LXMF hashes (monitors)
    admin_allowed: tuple[str, ...] = ()       # hex LXMF hashes (admins)
    retention_days: int = 90
    reject_older_than_sec: int = 86400
    reject_future_skew_sec: int = 300
    stats_refresh_sec: int = 60
    maintenance_hour: int = 3                  # local hour (0-23) for daily retention
    reticulum_config_dir: Optional[str] = None
    propagation: PropagationConfig = None

    def __post_init__(self):
        if self.propagation is None:
            self.propagation = PropagationConfig()

    @property
    def database_path(self) -> str:
        if self.database:
            return os.path.expanduser(self.database)
        return os.path.join(os.path.expanduser(self.storage_dir), "collector.db")

    @property
    def identity_path(self) -> str:
        return os.path.join(os.path.expanduser(self.storage_dir), "identity")


def _split_hashes(value: str) -> tuple[str, ...]:
    return tuple(h.strip() for h in value.replace("\n", ",").split(",") if h.strip())


def load(path: str) -> CollectorConfig:
    cp = parser()
    if not cp.read(os.path.expanduser(path)):
        raise ConfigError(f"config file not found: {path}")

    mode = cp.get("allowlist", "mode", fallback="explicit").lower()
    if mode not in ("explicit", "open"):
        raise ConfigError(f"[allowlist] mode must be explicit or open, got {mode!r}")

    maintenance_hour = cp.getint("maintenance", "hour", fallback=3)
    if not 0 <= maintenance_hour <= 23:
        raise ConfigError(f"[maintenance] hour must be 0-23, got {maintenance_hour}")

    return CollectorConfig(
        storage_dir=cp.get("storage", "dir", fallback="~/.watchersattherim/collector"),
        database=cp.get("storage", "database", fallback=None),
        bind=cp.get("collector", "bind", fallback="0.0.0.0"),
        http_port=cp.getint("collector", "http_port", fallback=8080),
        http_api=cp.getboolean("collector", "http_api", fallback=False),
        allowlist_mode=mode,
        allowed=_split_hashes(cp.get("allowlist", "allowed", fallback="")),
        admin_allowed=_split_hashes(cp.get("admin", "allowed", fallback="")),
        retention_days=cp.getint("storage", "retention_days", fallback=90),
        reject_older_than_sec=parse_duration(cp.get("ingest", "reject_older_than", fallback="24h")),
        reject_future_skew_sec=parse_duration(cp.get("ingest", "reject_future_skew", fallback="5m")),
        stats_refresh_sec=parse_duration(cp.get("stats", "refresh_interval", fallback="60")),
        maintenance_hour=maintenance_hour,
        reticulum_config_dir=cp.get("reticulum", "config_dir", fallback=None),
        propagation=propagation_config.from_parser(cp),
    )
