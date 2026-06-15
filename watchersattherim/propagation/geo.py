"""Geometry helpers for the propagation layer.

Maidenhead locator <-> lat/lon, great-circle distance, and resolution naming.
Self-contained (stdlib math only) so the propagation layer has no cross-package
dependency on the monitor.
"""

from __future__ import annotations

import math

# resolution name -> Maidenhead character count
_RESOLUTION = {"coarse": 2, "medium": 4, "fine": 6}

_EARTH_KM = 6371.0088
_KM_PER_MI = 1.609344


def resolution_chars(resolution: str) -> int:
    try:
        return _RESOLUTION[resolution.lower()]
    except (KeyError, AttributeError):
        raise ValueError(f"resolution must be coarse|medium|fine, got {resolution!r}")


def grid_center(grid: str) -> tuple[float, float]:
    """(lat, lon) of the center of a 2-, 4-, or 6-char Maidenhead locator."""
    g = grid.strip().upper()
    if len(g) not in (2, 4, 6) or not g[0].isalpha() or not g[1].isalpha():
        raise ValueError(f"invalid Maidenhead locator: {grid!r}")

    lon = -180.0 + (ord(g[0]) - ord("A")) * 20.0
    lat = -90.0 + (ord(g[1]) - ord("A")) * 10.0

    if len(g) == 2:
        return round(lat + 5.0, 4), round(lon + 10.0, 4)

    lon += int(g[2]) * 2.0
    lat += int(g[3]) * 1.0
    if len(g) == 4:
        return round(lat + 0.5, 4), round(lon + 1.0, 4)

    lon += (ord(g[4]) - ord("A")) * (2.0 / 24.0)
    lat += (ord(g[5]) - ord("A")) * (1.0 / 24.0)
    return round(lat + (1.0 / 24.0) / 2.0, 4), round(lon + (2.0 / 24.0) / 2.0, 4)


def latlon_to_grid(lat: float, lon: float, precision: int = 6) -> str:
    """Encode (lat, lon) to a Maidenhead locator of the given precision (2/4/6)."""
    lat = min(89.9999, max(-90.0, float(lat))) + 90.0
    lon = min(179.9999, max(-180.0, float(lon))) + 180.0

    out = chr(int(lon // 20) + ord("A")) + chr(int(lat // 10) + ord("A"))
    if precision == 2:
        return out
    out += str(int((lon % 20) // 2)) + str(int((lat % 10) // 1))
    if precision == 4:
        return out
    out += chr(int((lon % 2) / (2.0 / 24.0)) + ord("A"))
    out += chr(int((lat % 1) / (1.0 / 24.0)) + ord("A"))
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def to_km(distance: float, units: str) -> float:
    """Normalize a distance in the given units to km."""
    return distance * _KM_PER_MI if units == "mi" else distance


def parse_point(value: str, precision: int = 6) -> str:
    """Resolve a point parameter (a grid, or a ``lat,lon`` pair) to a grid."""
    if "," in value:
        lat_s, lon_s = value.split(",", 1)
        try:
            return latlon_to_grid(float(lat_s), float(lon_s), precision)
        except ValueError:
            raise ValueError(f"invalid lat,lon: {value!r}")
    g = value.strip().upper()
    grid_center(g)  # validates
    return g


def point_dict(grid: str) -> dict:
    """A {grid, lat, lon} echo object for a locator."""
    lat, lon = grid_center(grid)
    return {"grid": grid, "lat": lat, "lon": lon}


def grid_distance_km(a: str, b: str) -> float:
    """Great-circle distance in km between the centers of two locators."""
    (la, lo), (lb, lob) = grid_center(a), grid_center(b)
    return haversine_km(la, lo, lb, lob)


def convert_km(km: float, units: str = "km") -> float:
    """Convert a distance in km to the requested units, rounded for display."""
    return round(km / _KM_PER_MI, 1) if units == "mi" else round(km, 1)


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing in degrees true (0-360) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlam)
    return round((math.degrees(math.atan2(y, x)) + 360) % 360, 1)


def grid_bearing(a: str, b: str) -> float:
    """Initial bearing in degrees true between the centers of two locators."""
    (la, lo), (lb, lob) = grid_center(a), grid_center(b)
    return bearing(la, lo, lb, lob)
