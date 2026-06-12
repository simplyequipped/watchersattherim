#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _watr

_watr.header("Band Activity")
band = _watr.request_var("band", "")
hours = _watr.request_var("hours", "1")

print("Recent activity on a band (e.g. 20m, 40m).")
print("")
print("Band   `B444`<band`{}>`b".format(band))
print("Hours  `B444`<6|hours`{}>`b".format(hours))
print("")
print("`[Search`:/page/band.mu`band|hours]")
print("")

if band:
    print("-")
    try:
        _watr.render(_watr.q_band(band, _watr.as_int(hours, 1)))
    except Exception:
        print("`F900Collector database not available.`f")
