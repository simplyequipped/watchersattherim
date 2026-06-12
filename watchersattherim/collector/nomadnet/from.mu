#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _watr

_watr.header("From Grid")
grid = _watr.request_var("grid", "")
hours = _watr.request_var("hours", "4")

print("All paths transmitted from a grid square.")
print("")
print("Grid   `B444`<grid`{}>`b".format(grid))
print("Hours  `B444`<6|hours`{}>`b".format(hours))
print("")
print("`[Search`:/page/from.mu`grid|hours]")
print("")

if grid:
    print("-")
    try:
        _watr.render(_watr.q_from(grid, _watr.as_int(hours, 4)))
    except Exception:
        print("`F900Collector database not available.`f")
