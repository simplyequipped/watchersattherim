"""Query command dispatch, shared by the LXMF and HTTP query layers.

``dispatch`` maps a command name + params to a query result, raising
``CommandError`` with an error code on a bad command or parameters.
"""

from __future__ import annotations

from . import queries

# error codes
SUCCESS = 0
INVALID_COMMAND = 1
INVALID_PARAMS = 2
NOT_AUTHORIZED = 3
INTERNAL_ERROR = 4
RATE_LIMITED = 5

COMMANDS = (
    "path_query", "from_grid", "to_grid", "band_activity",
    "monitor_list", "monitor_info", "stats",
)


class CommandError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message)


def _req(params: dict, key: str) -> str:
    value = params.get(key)
    if value in (None, ""):
        raise CommandError(INVALID_PARAMS, f"missing parameter: {key}")
    return value


def _int(params: dict, key: str, default: int) -> int:
    value = params.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CommandError(INVALID_PARAMS, f"invalid integer parameter: {key}")


def _hex(value: str) -> str:
    try:
        bytes.fromhex(value)
    except ValueError:
        raise CommandError(INVALID_PARAMS, f"invalid hex address: {value}")
    return value


def dispatch(conn, stats, command: str, params: dict, *, now: int) -> dict:
    if command == "path_query":
        return queries.path_query(
            conn, tx_grid=_req(params, "tx_grid"), rx_grid=_req(params, "rx_grid"),
            hours=_int(params, "hours", queries.DEFAULT_HOURS),
            band=params.get("band"), now=now,
        )
    if command == "from_grid":
        return queries.from_grid(
            conn, grid=_req(params, "grid"),
            hours=_int(params, "hours", queries.DEFAULT_HOURS), now=now,
        )
    if command == "to_grid":
        return queries.to_grid(
            conn, grid=_req(params, "grid"),
            hours=_int(params, "hours", queries.DEFAULT_HOURS), now=now,
        )
    if command == "band_activity":
        return queries.band_activity(
            conn, band=_req(params, "band"), hours=_int(params, "hours", 1), now=now,
        )
    if command == "monitor_list":
        return queries.monitor_list(conn, now=now)
    if command == "monitor_info":
        return queries.monitor_info(conn, address=_hex(_req(params, "address")), now=now)
    if command == "stats":
        return stats.snapshot()
    raise CommandError(INVALID_COMMAND, f"unknown command: {command}")
