#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _watr

_watr.header("About")
print("Watchers At The Rim is a receive-only FT8 propagation observatory built on")
print("Reticulum and LXMF. Geographically distributed monitor nodes passively")
print("decode FT8 and report observed signal paths to this collector.")
print("")
print(">What you can query")
print("  An `!observation`! is one path: a transmitting location, a receiving")
print("  location, an SNR, a frequency, and a time. Direct observations end at a")
print("  monitor; indirect observations are paths between two other stations,")
print("  recovered from overheard signal reports.")
print("")
print(">Privacy")
print("  All data is from public over-the-air FT8 transmissions. The observatory")
print("  records propagation paths and conditions, not station activity;")
print("  callsigns are off by default.")
print("")
print("`[Back to queries`:/page/index.mu]")
