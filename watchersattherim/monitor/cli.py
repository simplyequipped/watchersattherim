"""Command-line entry point for the watchersattherim monitor."""

from __future__ import annotations

import argparse
import configparser
import os
import signal
import sys
from typing import Optional

from .config import ConfigError, load
from .monitor import Monitor
from .transport import StdoutSender


def _storage_dir(config_path: str, default: str) -> str:
    """Read [storage] dir from the config leniently, without full validation."""
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#",))
    cp.read(os.path.expanduser(config_path))
    return cp.get("storage", "dir", fallback=default)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watchersattherim",
        description="FT8 propagation observatory monitor (ft8mon -> LXMF telemetry)",
    )
    parser.add_argument("-c", "--config", default="monitor.ini",
                        help="path to the INI config (default: monitor.ini)")
    parser.add_argument("-i", "--identity", action="store_true",
                        help="print this node's LXMF address (creating the identity if needed) and exit")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print each ft8mon decode to stdout as it arrives")
    parser.add_argument("--dry-run", action="store_true",
                        help="print telemetry batches as JSON instead of sending via LXMF")
    args = parser.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    if args.identity:
        from .config import Storage
        from ..common.identity import print_identity
        print_identity(os.path.join(os.path.expanduser(_storage_dir(args.config, Storage().dir)), "identity"))
        return 0

    try:
        config = load(args.config)
    except ConfigError as e:
        log(f"config error: {e}")
        return 2

    os.makedirs(os.path.expanduser(config.storage.dir), exist_ok=True)

    if args.dry_run:
        sender = StdoutSender()
        config.collector.send_interval = 15
    else:
        from .sender import make_lxmf_sender
        sender = make_lxmf_sender(config, log=log)

    monitor = Monitor(config, sender, logger=log, verbose=args.verbose)

    def shutdown(_signum, _frame):
        log("shutting down…")
        monitor.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log(f"watchersattherim monitor starting ({len(config.receivers)} receiver(s))")
    monitor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
