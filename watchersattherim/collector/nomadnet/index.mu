#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _watr

_watr.header("Watchers At The Rim")
print("A receive-only FT8 propagation observatory. Query observed signal paths")
print("collected from monitor nodes over Reticulum/LXMF.")
print("")
try:
    st = _watr.q_stats()
    print(">Now")
    print(f"  observations  {st['total']}  (last 24h {st['obs_24h']})")
    print(f"  monitors      {st['active_monitors']} active / {st['total_monitors']} total")
    print("")
except Exception:
    print("`F900Collector database not available.`f")
    print("")
print(">Queries")
print("  `[Path - grid to grid`:/page/path.mu]")
print("  `[From a grid`:/page/from.mu]")
print("  `[To a grid`:/page/to.mu]")
print("  `[Band activity`:/page/band.mu]")
print("  `[Active monitors`:/page/monitors.mu]")
print("  `[About`:/page/about.mu]")
