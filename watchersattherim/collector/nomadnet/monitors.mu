#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _watr

_watr.header("Active Monitors")
print("Monitor nodes that reported in the last 24 hours.")
print("")
try:
    rows = _watr.q_monitors()
except Exception:
    print("`F900Collector database not available.`f")
    sys.exit(0)

if not rows:
    print("No active monitors.")
else:
    print("`!  grid    last seen (UTC)        address`!")
    for r in rows:
        print("  {:6}  {}  {}".format(
            r["grid"] or "-", _watr.hhmmss(r["last_seen"]), r["address"].hex()))
