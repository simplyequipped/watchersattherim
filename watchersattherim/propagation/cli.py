"""Local query CLI for the collector's observation store.

Runs any query/propagation command against a collector database read-only, without
the HTTP/LXMF surface - for operators on the collector host and for poking at
snapshots. Over-the-network queries use ``watr-query`` instead.

    watr-propagation channel origin=FM19 dest=EM
    watr-propagation map origin=FM19 radius=5000 resolution=medium --csv
    watr-propagation trend/band/hour band=40m --time local --full

The database is chosen from --db, else --config (a collector INI), else
$WATR_COLLECTOR_DB, else the default collector path. --config also supplies the
propagation caps and default timezone, matching the running collector.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from ..collector import config as collector_config
from ..collector.commands import CommandError, dispatch
from ..collector.config import CollectorConfig
from ..collector.stats import Stats
from .config import PropagationConfig


def _resolve_db(args) -> str:
    if args.db:
        return os.path.expanduser(args.db)
    if args.config:
        return collector_config.load(args.config).database_path
    env = os.environ.get("WATR_COLLECTOR_DB")
    return os.path.expanduser(env) if env else CollectorConfig().database_path


def _local_timezone() -> Optional[str]:
    """Best-effort local IANA zone (stdlib only): $TZ, then the /etc/localtime
    symlink. Returns None if neither yields a name a ZoneInfo accepts (e.g. on
    systems that copy rather than symlink /etc/localtime, or on Windows)."""
    import zoneinfo
    candidates = []
    if os.environ.get("TZ"):
        candidates.append(os.environ["TZ"])
    try:
        resolved = Path("/etc/localtime").resolve().as_posix()
        if "zoneinfo/" in resolved:
            candidates.append(resolved.split("zoneinfo/")[-1])
    except Exception:
        pass
    for c in candidates:
        try:
            zoneinfo.ZoneInfo(c)
            return c
        except Exception:
            continue
    return None


def _resolve_timezone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = value.strip()
    if v.lower() == "utc":
        return "UTC"
    if v.lower() == "local":
        tz = _local_timezone()
        if tz is None:
            raise ValueError("could not determine local timezone; pass --timezone <IANA name>")
        return tz
    return v


def _connect_ro(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# --- output -----------------------------------------------------------------

def _truncate(result, top):
    import copy
    r = copy.deepcopy(result)

    def trim(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, list) and len(v) > top:
                    obj[k] = v[:top] + [f"... ({len(v) - top} more)"]
                elif isinstance(v, (dict, list)):
                    trim(v)
        elif isinstance(obj, list):
            for it in obj:
                trim(it)

    trim(r)
    return r


def _rows(result: dict) -> list[dict]:
    """The primary tabular list of a result, for CSV."""
    if "cells" in result:
        return result["cells"]
    if "items" in result:
        return result["items"]
    if "monitors" in result:
        return result["monitors"]
    if "bands" in result:
        bands = result["bands"]
        if isinstance(bands, list):
            return bands
        # trend: {band: {items: [...]}}; channel: {band: {ft8, wspr}}
        rows = []
        for b, v in bands.items():
            if isinstance(v, dict) and "items" in v:
                rows.extend({"band": b, **it} for it in v["items"])
            else:
                rows.append({"band": b, **v})
        return rows
    if result.get("monitor"):
        return [result["monitor"]]
    return [result]


def _flatten(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out[f"{k}.{k2}"] = v2
        elif isinstance(v, list):
            out[k] = json.dumps(v)
        else:
            out[k] = v
    return out


def _to_csv(result: dict, top: Optional[int]) -> str:
    rows = [_flatten(r) for r in _rows(result)]
    if top:
        rows = rows[:top]
    if not rows:
        return ""
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def _fmt_x(value, key: str) -> str:
    if key == "time":
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value, timezone.utc).strftime("%m/%d-%Hz")
    return str(value)


def _chart(result: dict, metric: Optional[str], command: str) -> str:
    """ASCII bar chart of a trend result; bar = the chosen numeric metric."""
    unit = result.get("unit")
    if "items" in result:
        sections = [(None, result["items"])]
    elif isinstance(result.get("bands"), dict):
        sections = [(b, v.get("items", [])) for b, v in result["bands"].items()]
    else:
        raise ValueError("--chart is only supported for trend commands")
    sections = [(lbl, items) for lbl, items in sections if items]
    if not sections:
        return "no data to chart"

    sample = sections[0][1][0]
    x_key = unit if unit in sample else ("time" if "time" in sample else None)
    if x_key is None:
        raise ValueError("could not determine the chart axis")
    available = [k for k in sample if k != x_key and isinstance(sample[k], (int, float))]
    metric = metric or ("quality" if "quality" in available else available[0])
    if metric not in available:
        raise ValueError(f"metric must be one of: {', '.join(available)}")

    scope = (f"{result['origin']['grid']} -> {result['dest']['grid']}"
             if "origin" in result else result.get("band", ""))
    col = f"{metric} ({result['units']})" if metric == "distance" and result.get("units") else metric
    wx = 10 if x_key == "time" else 6
    wv = max(11, len(col) + 2)
    bar = 46

    out = [f"{command}  {scope}   metric: {metric}",
           f"available metrics: {', '.join(available)}"]
    for label, items in sections:
        if label:
            out.append(f"[{label}]")
        vals = [it[metric] for it in items]
        base = min(vals) if min(vals) < 0 else 0          # snr (negative) bars relative to min
        span = (max(vals) - base) or 1
        out.append(f"  {x_key:^{wx}}{col:^{wv}}  chart")
        for it in items:
            v = it[metric]
            length = round(bar * (v - base) / span)
            out.append(f"  {_fmt_x(it[x_key], x_key):^{wx}}{'%g' % v:^{wv}}  {'#' * length}")
    return "\n".join(out)


_EPILOG = """\
commands:
  raw observation queries:
    path               observations on a path          origin, dest, [window=2h], [band]
    from               observations sent from a grid   grid|lat,lon, [window=2h]
    to                 observations heard at a grid    grid|lat,lon, [window=2h]
    band               recent activity on a band       band, [window=1h]
    monitor            one monitor's detail            address
    monitors           known/active monitors
    stats              collector snapshot

  propagation analysis queries:
    channel            best band(s) to reach a target right now
                          origin, dest, [window=30m], [bands], [at], [widen], [units]
    channel/anomaly    channel now vs the recent normal for this hour
                          origin, dest, [window=30m], [baseline=7d], [at], [timezone], [units]
    trend/path/hour    path trend over time
           .../month      origin, dest, [bands], [timezone], [hour/month/year], [units]
           .../year
    trend/path/anomaly chronological hourly deviation series (event detection)
                          origin, dest, [bands], [window=7d | start, end], [timezone]
    trend/band/hour    band activity over time
           .../month      band, [origin, radius, units], [hour/month/year], [timezone]
           .../year
    map                activity field over an area (one band)
                          origin, [radius=2000], [units], [band=40m], [resolution], [window=1h]
    coverage           reachability field from/to a point
                          origin XOR dest, [radius=2000], [units], [band], [resolution], [window=30m]

parameters (key=value):
  origin, dest, grid   Maidenhead grid (2/4/6-character precision) or "lat,lon" (grid=FN19 or grid=40.5,-74.0)
  bands                comma-separated list of band designators ("40m,20m")
  band                 band designators ("40m")
  window               lookback duration: 30m, 2h, 7d
  hour, month, year    optional filter for trend queries (hour=17 | month=7)
  start, end           Unix timestamp
  units                km (default) or mi
  resolution           coarse | medium | fine  (maps to Maidenhead grid: field | square | subsquare)
  radius               radius from origin in distance units
  widen                reduce resolution if no results at given grid resolution: true (default) | false
  at                   reference unix time (default: most recent observation)
"""


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="watr-propagation",
        description="run a query/propagation command against a collector database",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("command", help="(see below)")
    p.add_argument("params", nargs="*", help="key=value query parameters (see below)")
    p.add_argument("--db", help="collector database path")
    p.add_argument("--config", help="collector ini file path (for db path + propagation settings)")
    p.add_argument("--timezone", dest="timezone",
                   help="timezone for trend/anomaly: 'local', 'utc', or an IANA name")
    p.add_argument("--now", type=int, help="reference unix time (default: most recent observation)")
    p.add_argument("--top", type=int, help="return only N first results")
    p.add_argument("--csv", action="store_true", help="output to terminal in CSV")
    p.add_argument("--json", action="store_true", help="output to terminal in JSON (default)")
    p.add_argument("--chart", nargs="?", const="", default=None,
                   help="ASCII chart of a trend result + optional metric (ex. --chart quality)")
    args = p.parse_args(argv)

    try:
        params = dict(item.split("=", 1) for item in args.params)
    except ValueError:
        print("parameters must be key=value", file=sys.stderr)
        return 2
    try:
        tz = _resolve_timezone(args.timezone)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if tz:
        params["timezone"] = tz

    prop = collector_config.load(args.config).propagation if args.config else PropagationConfig()
    try:
        conn = _connect_ro(_resolve_db(args))
    except FileNotFoundError as e:
        print(f"error: database not found: {e}", file=sys.stderr)
        return 2

    now = args.now or conn.execute(
        "SELECT MAX(observed_at) FROM observations").fetchone()[0]
    if now is None:
        print("error: database has no observations", file=sys.stderr)
        return 2

    try:
        result = dispatch(conn, Stats(conn), args.command, params, now=now + 5, propagation=prop)
    except CommandError as e:
        print(f"error {e.code}: {e}", file=sys.stderr)
        return 2

    if args.chart is not None:
        try:
            print(_chart(result, args.chart or None, args.command))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    elif args.csv:
        sys.stdout.write(_to_csv(result, args.top))
    else:
        print(json.dumps(_truncate(result, args.top) if args.top else result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
