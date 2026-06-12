"""Command-line entry point for the watchersattherim collector."""

from __future__ import annotations

import argparse
import configparser
import os
import signal
import sys
import threading
import time
from typing import Optional

from ..common.config import ConfigError
from . import http, storage
from .config import CollectorConfig, load
from .stats import Stats


def _storage_dir(config_path: str, default: str) -> str:
    """Read [storage] dir from the config leniently, without full validation."""
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#",))
    cp.read(os.path.expanduser(config_path))
    return cp.get("storage", "dir", fallback=default)

MAINTENANCE_TICK = 3600  # cap on the loop wait, so retention runs at least hourly-checked


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _seed_allowlist(conn, config: CollectorConfig, now: int) -> None:
    for h in config.allowed:
        try:
            storage.set_allowed(conn, bytes.fromhex(h), True, now=now)
        except ValueError:
            _log(f"skipping invalid allowlist hash: {h}")


def _date(lt: time.struct_time) -> tuple:
    return (lt.tm_year, lt.tm_mon, lt.tm_mday)


def _prune_due(lt: time.struct_time, last_date, hour: int) -> bool:
    """True once per day, on the first check at or after the local maintenance hour."""
    return _date(lt) != last_date and lt.tm_hour >= hour


def _run_admin(args, conn) -> Optional[int]:
    """Handle allowlist admin commands. Returns an exit code, or None if none ran."""
    now = int(time.time())
    target = args.allow or args.deny
    if target is not None:
        try:
            addr = bytes.fromhex(target)
        except ValueError:
            _log(f"invalid address: {target}")
            return 2
        storage.set_allowed(conn, addr, bool(args.allow), now=now)
        print(("allowed " if args.allow else "denied ") + target)
        return 0
    qtarget = args.block or args.unblock
    if qtarget is not None:
        try:
            addr = bytes.fromhex(qtarget)
        except ValueError:
            _log(f"invalid address: {qtarget}")
            return 2
        storage.set_query_blocked(conn, addr, bool(args.block), now=now)
        print(("query-blocked " if args.block else "query-unblocked ") + qtarget)
        return 0
    if args.list_monitors:
        rows = storage.list_monitors(conn)
        if not rows:
            print("(no monitors known)")
        for r in rows:
            flag = "allowed" if r["allowed"] else "denied "
            print(f"{r['address'].hex()}  {flag}  grid={r['grid'] or '-':<6}  "
                  f"last_seen={r['last_seen']}  {r['sw_version'] or ''}".rstrip())
        return 0
    return None


def _setup_lxmf(config: CollectorConfig, conn, lock, stats):
    import RNS

    from ..common import lxmf
    from .listener import CollectorListener

    router, source = lxmf.setup(
        identity_path=config.identity_path,
        storage_dir=config.storage_dir,
        display_name="watchersattherim collector",
        reticulum_config_dir=config.reticulum_config_dir,
    )
    listener = CollectorListener(router, source, conn, lock, stats, config, time.time, _log)
    _log(f"collector LXMF address: {RNS.prettyhexrep(source.hash)}")
    return listener


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watr-collector",
        description="FT8 propagation observatory collector",
    )
    parser.add_argument("-c", "--config", default="collector.ini",
                        help="path to the INI config (default: collector.ini)")
    parser.add_argument("-i", "--identity", action="store_true",
                        help="print this collector's LXMF address (creating the identity if needed) and exit")
    parser.add_argument("--allow", metavar="HASH",
                        help="add a monitor LXMF address to the allowlist and exit")
    parser.add_argument("--deny", metavar="HASH",
                        help="remove a monitor from the allowlist and exit")
    parser.add_argument("--list-monitors", action="store_true",
                        help="list known monitors and exit")
    parser.add_argument("--block", metavar="HASH",
                        help="deny an address from querying and exit")
    parser.add_argument("--unblock", metavar="HASH",
                        help="re-allow an address to query and exit")
    parser.add_argument("--no-lxmf", action="store_true",
                        help="run the HTTP query API only, without LXMF ingest (dev)")
    args = parser.parse_args(argv)

    if args.identity:
        from ..common.identity import print_identity
        storage_dir = _storage_dir(args.config, CollectorConfig().storage_dir)
        print_identity(os.path.join(os.path.expanduser(storage_dir), "identity"))
        return 0

    try:
        config = load(args.config)
    except ConfigError as e:
        _log(f"config error: {e}")
        return 2

    os.makedirs(os.path.expanduser(config.storage_dir), exist_ok=True)
    conn = storage.connect(config.database_path)

    admin_rc = _run_admin(args, conn)
    if admin_rc is not None:
        return admin_rc

    lock = threading.Lock()
    stats = Stats(conn)
    with lock:
        _seed_allowlist(conn, config, int(time.time()))
        stats.refresh()

    stop = threading.Event()

    server = None
    if config.http_api:
        server = http.make_server(conn, lock, stats, bind=config.bind, port=config.http_port)
        threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
        _log(f"HTTP query API on {config.bind}:{config.http_port}")

    if args.no_lxmf:
        _log("LXMF ingest disabled (--no-lxmf)")
    else:
        _setup_lxmf(config, conn, lock, stats)

    def shutdown(_signum, _frame):
        _log("shutting down…")
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    _log(f"watchersattherim collector started (allowlist: {config.allowlist_mode})")

    # Don't prune on startup if we're already past today's maintenance hour;
    # wait for the next scheduled run.
    start = time.localtime()
    last_prune_date = _date(start) if start.tm_hour >= config.maintenance_hour else None

    while not stop.wait(min(config.stats_refresh_sec, MAINTENANCE_TICK)):
        now = int(time.time())
        lt = time.localtime(now)
        with lock:
            stats.refresh(now)
            if _prune_due(lt, last_prune_date, config.maintenance_hour):
                deleted = storage.prune(conn, config.retention_days, now=now)
                last_prune_date = _date(lt)
                _log(f"retention: pruned {deleted} observations "
                     f"(daily at {config.maintenance_hour:02d}:00 local)")

    if server is not None:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
