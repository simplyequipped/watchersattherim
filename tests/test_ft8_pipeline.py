"""Tests for the FT8 ingest pipeline: blacklist ordering vs cache, SNR/distance ceiling."""

from watchersattherim.monitor.cache import CallsignCache
from watchersattherim.monitor.config import Blacklist
from watchersattherim.monitor.ft8_pipeline import ingest
from watchersattherim.monitor.observations import snr_exceeds_ceiling

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


# --- SNR/distance ceiling -------------------------------------------------

def test_snr_exceeds_ceiling_step_function():
    c = ((9000, 15), (13000, -15), (16000, -20))
    assert not snr_exceeds_ceiling(c, 5000, 30)     # below smallest distance: no cap
    assert not snr_exceeds_ceiling(c, 10000, 10)    # cap 15, 10 ok
    assert snr_exceeds_ceiling(c, 10000, 20)        # cap 15, 20 too strong
    assert snr_exceeds_ceiling(c, 14000, -10)       # cap -15, -10 too strong
    assert not snr_exceeds_ceiling(c, 14000, -20)   # cap -15, -20 ok (weak long-haul)
    assert snr_exceeds_ceiling(c, 17000, -18)       # cap -20, -18 too strong
    assert not snr_exceeds_ceiling((), 17000, 30)   # empty ceiling never blocks


# RF73 (New Zealand) is ~14200 km from FN19: strong there is impossible.
STRONG_FAR = "024015  -5 174  1.31 1000.0 CQ W3GO RF73"
WEAK_FAR   = "024015 -22 174  1.31 1000.0 CQ W3GO RF73"


def test_strong_at_distance_dropped_and_not_learned():
    cache = CallsignCache()
    out = ingest(STRONG_FAR, 7074000, "FN19", cache, snr_ceiling=((13000, -15),))
    assert out == []                          # fabricated, dropped
    assert cache.lookup("W3GO") is None        # and never cached


def test_weak_at_distance_kept():
    cache = CallsignCache()
    out = ingest(WEAK_FAR, 7074000, "FN19", cache, snr_ceiling=((13000, -15),))
    assert len(out) == 1                       # plausible weak long-path, kept
    assert cache.lookup("W3GO") == "RF73"
