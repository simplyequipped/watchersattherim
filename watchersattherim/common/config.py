"""Shared config primitives used by both the monitor and collector configs."""

from __future__ import annotations

import configparser
import re


class ConfigError(Exception):
    pass


_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value) -> int:
    """Parse '2h' / '120m' / '1d' / '7200' to seconds."""
    if isinstance(value, int):
        return value
    m = re.fullmatch(r"(\d+)\s*([smhd]?)", str(value).strip())
    if not m:
        raise ConfigError(f"invalid duration: {value!r}")
    return int(m[1]) * _UNITS[m[2] or "s"]


def fmt_duration(seconds: int) -> str:
    """Inverse of parse_duration for the largest whole unit ('1800' -> '30m')."""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0 and seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def parser() -> configparser.ConfigParser:
    """A ConfigParser configured the way watchersattherim reads INI files."""
    return configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#",))
