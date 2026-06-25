"""Tests for the FT8 ingest pipeline, focused on blacklist ordering vs the cache."""

from watchersattherim.monitor.cache import CallsignCache
from watchersattherim.monitor.config import Blacklist
from watchersattherim.monitor.ft8_pipeline import ingest

# CQ from W3GO advertising grid RF73, at dial 7074000 + 1000 Hz audio offset.
LINE = "024015   0 174  1.31 1000.0 CQ W3GO RF73"


def test_blacklisted_grid_yields_nothing_and_is_not_learned():
    cache = CallsignCache()
    bl = Blacklist(grids=frozenset({"RF73"}))
    out = ingest(LINE, 7074000, "FN19", cache, blacklist=bl)
    assert out == []                          # no observation
    assert cache.lookup("W3GO") is None       # and the cache stays clean


def test_without_blacklist_the_grid_is_learned():
    cache = CallsignCache()
    out = ingest(LINE, 7074000, "FN19", cache)
    assert len(out) == 1                       # control: normal direct observation
    assert cache.lookup("W3GO") == "RF73"      # and normally learned


def test_blacklisted_freq_is_not_learned():
    cache = CallsignCache()
    bl = Blacklist(freqs=((7074990, 7075010),))   # covers dial 7074000 + 1000 Hz
    assert ingest(LINE, 7074000, "FN19", cache, blacklist=bl) == []
    assert cache.lookup("W3GO") is None
