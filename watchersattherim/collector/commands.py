"""Query command dispatch, shared by the LXMF and HTTP query layers.

``dispatch`` maps a command name + params to a query result, raising
``CommandError`` with an error code on a bad command or parameters. Raw
observation queries and propagation queries share this one dispatch, so both are
served identically over HTTP and LXMF.
"""

from __future__ import annotations

from . import queries
from ..common.config import ConfigError, parse_duration
from ..propagation import channel, field, geo, trend
from ..propagation.config import PropagationConfig

# error codes
SUCCESS = 0
INVALID_COMMAND = 1
INVALID_PARAMS = 2
NOT_AUTHORIZED = 3
INTERNAL_ERROR = 4
RATE_LIMITED = 5

RAW_COMMANDS = ("path", "from", "to", "band", "monitors", "monitor", "stats")
PROPAGATION_COMMANDS = (
    "channel", "channel/anomaly",
    "trend/path/hour", "trend/path/month", "trend/path/year", "trend/path/anomaly",
    "trend/band/hour", "trend/band/month", "trend/band/year",
    "map", "coverage",
)
COMMANDS = RAW_COMMANDS + PROPAGATION_COMMANDS


class CommandError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message)


# --- param helpers ----------------------------------------------------------

def _req(params: dict, key: str) -> str:
    value = params.get(key)
    if value in (None, ""):
        raise CommandError(INVALID_PARAMS, f"missing parameter: {key}")
    return value


def _hex(value: str) -> str:
    try:
        bytes.fromhex(value)
    except ValueError:
        raise CommandError(INVALID_PARAMS, f"invalid hex address: {value}")
    return value


def _window(params: dict, default_sec: int, cap_sec: int) -> int:
    raw = params.get("window")
    if raw in (None, ""):
        return min(default_sec, cap_sec)
    try:
        return min(parse_duration(raw), cap_sec)
    except ConfigError:
        raise CommandError(INVALID_PARAMS, f"invalid window: {raw}")


def _named_point(params: dict, key: str) -> str:
    try:
        return geo.parse_point(_req(params, key))
    except ValueError as e:
        raise CommandError(INVALID_PARAMS, str(e))


def _single_point(params: dict) -> str:
    """from/to take grid or lat+lon as separate args."""
    if params.get("grid"):
        try:
            return geo.parse_point(params["grid"])
        except ValueError as e:
            raise CommandError(INVALID_PARAMS, str(e))
    lat, lon = params.get("lat"), params.get("lon")
    if lat and lon:
        try:
            return geo.latlon_to_grid(float(lat), float(lon))
        except ValueError:
            raise CommandError(INVALID_PARAMS, f"invalid lat,lon: {lat},{lon}")
    raise CommandError(INVALID_PARAMS, "missing parameter: grid (or lat+lon)")


def _bands(params: dict):
    raw = params.get("bands")
    if not raw:
        return None
    return [b.strip() for b in raw.split(",") if b.strip()]


def _resolution(params: dict) -> str:
    res = params.get("resolution", "medium")
    try:
        geo.resolution_chars(res)
    except ValueError as e:
        raise CommandError(INVALID_PARAMS, str(e))
    return res


def _radius(params: dict, default: float, cap_km: float) -> tuple[float, str]:
    units = params.get("units", "km")
    if units not in ("km", "mi"):
        raise CommandError(INVALID_PARAMS, f"units must be km or mi, got {units}")
    raw = params.get("radius", default)
    try:
        radius_km = geo.to_km(float(raw), units)
    except (TypeError, ValueError):
        raise CommandError(INVALID_PARAMS, f"invalid radius: {raw}")
    return min(radius_km, cap_km), units


def _units(params: dict) -> str:
    u = params.get("units", "km")
    if u not in ("km", "mi"):
        raise CommandError(INVALID_PARAMS, f"units must be km or mi, got {u}")
    return u


def _ref_power(params: dict) -> int:
    raw = params.get("ref_power_dbm")
    if raw in (None, ""):
        return 37
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise CommandError(INVALID_PARAMS, f"invalid ref_power_dbm: {raw}")


def _rank(params: dict) -> str:
    v = str(params.get("rank", "ft8")).lower()
    if v not in ("ft8", "wspr"):
        raise CommandError(INVALID_PARAMS, f"rank must be ft8 or wspr, got {v}")
    return v


def _mode(params: dict) -> str:
    v = str(params.get("mode", "FT8")).upper()
    if v not in ("FT8", "WSPR"):
        raise CommandError(INVALID_PARAMS, f"mode must be FT8 or WSPR, got {v}")
    return v


def _at(params: dict):
    raw = params.get("at")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise CommandError(INVALID_PARAMS, f"invalid at: {raw}")


def _tz(params: dict, prop: PropagationConfig) -> str:
    return params.get("timezone") or prop.default_timezone


def _trend_filters(params: dict) -> dict:
    out = {}
    for key in ("hour", "month", "year"):
        if params.get(key) not in (None, ""):
            try:
                out[key] = int(params[key])
            except (TypeError, ValueError):
                raise CommandError(INVALID_PARAMS, f"invalid {key}: {params[key]}")
    return out


# --- dispatch ---------------------------------------------------------------

def dispatch(conn, stats, command: str, params: dict, *, now: int,
             propagation: PropagationConfig = None) -> dict:
    prop = propagation or PropagationConfig()

    # raw observation queries
    if command == "path":
        return queries.path_query(
            conn, tx_grid=_named_point(params, "origin"), rx_grid=_named_point(params, "dest"),
            window_sec=_window(params, 7200, prop.max_window_sec),
            band=params.get("band"), now=now,
        )
    if command == "from":
        return queries.from_grid(conn, grid=_single_point(params),
                                 window_sec=_window(params, 7200, prop.max_window_sec), now=now)
    if command == "to":
        return queries.to_grid(conn, grid=_single_point(params),
                               window_sec=_window(params, 7200, prop.max_window_sec), now=now)
    if command == "band":
        return queries.band_activity(conn, band=_req(params, "band"),
                                     window_sec=_window(params, 3600, prop.max_window_sec), now=now)
    if command == "monitors":
        return queries.monitor_list(conn, now=now)
    if command == "monitor":
        return queries.monitor_info(conn, address=_hex(_req(params, "address")), now=now)
    if command == "stats":
        return stats.snapshot()

    # propagation queries
    if command in PROPAGATION_COMMANDS:
        if not prop.enabled:
            raise CommandError(INVALID_COMMAND, "propagation queries are disabled")
        return _propagation(conn, command, params, now=now, prop=prop)

    raise CommandError(INVALID_COMMAND, f"unknown command: {command}")


def _propagation(conn, command, params, *, now, prop) -> dict:
    at = _at(params)
    at = now if at is None else at
    widen = params.get("widen", "true") != "false"
    if command == "channel":
        return channel.estimate(
            conn, origin=_named_point(params, "origin"), dest=_named_point(params, "dest"),
            bands=_bands(params), window_sec=_window(params, 1800, prop.max_window_sec),
            at=at, widen=widen, units=_units(params),
            ref_power_dbm=_ref_power(params), rank=_rank(params),
        )
    if command == "channel/anomaly":
        return channel.anomaly(
            conn, origin=_named_point(params, "origin"), dest=_named_point(params, "dest"),
            bands=_bands(params), window_sec=_window(params, 1800, prop.max_window_sec),
            baseline_sec=_window({"window": params.get("baseline")}, 7 * 86400, prop.max_window_sec)
            if params.get("baseline") else 7 * 86400,
            at=at, timezone=_tz(params, prop), widen=widen, units=_units(params),
        )
    if command.startswith("trend/path/"):
        unit = command.rsplit("/", 1)[1]
        if unit == "anomaly":
            return trend.path_anomaly(
                conn, origin=_named_point(params, "origin"), dest=_named_point(params, "dest"),
                bands=_bands(params),
                window_sec=_window(params, 7 * 86400, prop.max_window_sec),
                start=int(params["start"]) if params.get("start") else None,
                end=int(params["end"]) if params.get("end") else now,
                timezone=_tz(params, prop),
            )
        return trend.path(
            conn, origin=_named_point(params, "origin"), dest=_named_point(params, "dest"),
            unit=unit, bands=_bands(params), filters=_trend_filters(params),
            timezone=_tz(params, prop), max_window_sec=prop.max_window_sec, now=now,
            units=_units(params),
        )
    if command.startswith("trend/band/"):
        unit = command.rsplit("/", 1)[1]
        origin = params.get("origin")
        radius_km = None
        if origin:
            origin = _named_point(params, "origin")
            radius_km, _ = _radius(params, 2000, prop.max_radius_km)
        return trend.band(
            conn, band=params.get("band", "40m"), unit=unit,
            origin=origin, radius_km=radius_km, filters=_trend_filters(params),
            timezone=_tz(params, prop), max_window_sec=prop.max_window_sec, now=now,
            units=_units(params),
        )
    if command == "map":
        radius_km, units = _radius(params, 2000, prop.max_radius_km)
        return field.map_field(
            conn, origin=_named_point(params, "origin"), radius_km=radius_km, units=units,
            band=params.get("band", "40m"), mode=_mode(params), resolution=_resolution(params),
            window_sec=_window(params, 3600, prop.max_window_sec), at=at,
            max_cells=prop.max_cells,
        )
    if command == "coverage":
        if bool(params.get("origin")) == bool(params.get("dest")):
            raise CommandError(INVALID_PARAMS, "coverage needs exactly one of origin, dest")
        radius_km, units = _radius(params, 2000, prop.max_radius_km)
        origin = _named_point(params, "origin") if params.get("origin") else None
        dest = _named_point(params, "dest") if params.get("dest") else None
        return field.coverage(
            conn, origin=origin, dest=dest, radius_km=radius_km, units=units,
            band=params.get("band"), resolution=_resolution(params),
            window_sec=_window(params, 1800, prop.max_window_sec), at=at,
            max_cells=prop.max_cells,
            ref_power_dbm=_ref_power(params), rank=_rank(params),
        )
    raise CommandError(INVALID_COMMAND, f"unknown command: {command}")
