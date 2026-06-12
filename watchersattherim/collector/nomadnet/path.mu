#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _watr

_watr.header("Path Query")
tx = _watr.request_var("tx_grid", "")
rx = _watr.request_var("rx_grid", "")
hours = _watr.request_var("hours", "4")

print("Observed paths between two grid squares (4- or 6-character).")
print("")
print("TX grid  `B444`<tx_grid`{}>`b".format(tx))
print("RX grid  `B444`<rx_grid`{}>`b".format(rx))
print("Hours    `B444`<6|hours`{}>`b".format(hours))
print("")
print("`[Search`:/page/path.mu`tx_grid|rx_grid|hours]")
print("")

if tx and rx:
    print("-")
    try:
        _watr.render(_watr.q_path(tx, rx, _watr.as_int(hours, 4)))
    except Exception:
        print("`F900Collector database not available.`f")
