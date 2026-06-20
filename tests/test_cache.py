"""Tests for the callsign->grid cache and the per-decode pipeline."""

from watchersattherim.monitor.cache import CallsignCache
from watchersattherim.monitor.ft8_parser import classify, parse_line
from watchersattherim.monitor.ft8_pipeline import process_decode


def test_update_and_lookup():
    c = CallsignCache()
    c.update("KF9UG", "EN71", ts=1000.0)
    assert c.lookup("KF9UG", ts=1000.0) == "EN71"
    assert c.lookup("NOPE", ts=1000.0) is None
    e = c.entry("KF9UG")
    assert e.count == 1 and e.grid == "EN71"


def test_refresh_increments_count_and_updates_grid():
    c = CallsignCache()
    c.update("KF9UG", "EN71", ts=1000.0)
    c.update("KF9UG", "EN72", ts=1100.0)
    e = c.entry("KF9UG")
    assert e.count == 2 and e.grid == "EN72" and e.last_seen == 1100.0


def test_ttl_expiry():
    c = CallsignCache(entry_ttl_sec=7200)
    c.update("KF9UG", "EN71", ts=1000.0)
    assert c.lookup("KF9UG", ts=1000.0 + 7200) == "EN71"        # exactly at TTL
    assert c.lookup("KF9UG", ts=1000.0 + 7201) is None          # just past TTL
    assert len(c) == 0                                          # expired entry dropped


def test_lru_eviction():
    c = CallsignCache(max_entries=3)
    for i, call in enumerate(["A1AA", "B2BB", "C3CC"]):
        c.update(call, "FN42", ts=1000.0 + i)
    c.update("D4DD", "FN42", ts=2000.0)   # evicts oldest (A1AA)
    assert len(c) == 3
    assert c.lookup("A1AA", ts=2000.0) is None
    assert c.lookup("D4DD", ts=2000.0) == "FN42"


def test_lru_keeps_recently_updated():
    c = CallsignCache(max_entries=2)
    c.update("A1AA", "FN42", ts=1.0)
    c.update("B2BB", "FN42", ts=2.0)
    c.update("A1AA", "FN42", ts=3.0)      # refresh A -> B is now oldest
    c.update("C3CC", "FN42", ts=4.0)      # evicts B, not A
    assert c.lookup("A1AA", ts=4.0) == "FN42"
    assert c.lookup("B2BB", ts=4.0) is None


def test_bad_grid_ignored():
    c = CallsignCache()
    c.update("KF9UG", "ZZ99", ts=1000.0)  # not a valid Maidenhead field
    assert len(c) == 0


def test_prune():
    c = CallsignCache(entry_ttl_sec=100)
    c.update("A1AA", "FN42", ts=0.0)
    c.update("B2BB", "FN42", ts=1000.0)
    assert c.prune(ts=1050.0) == 1        # A expired, B alive
    assert c.lookup("B2BB", ts=1050.0) == "FN42"


def test_persistence_round_trip(tmp_path):
    path = str(tmp_path / "cache.db")
    c = CallsignCache()
    c.update("KF9UG", "EN71", ts=1000.0)
    c.update("KE2CUR", "FN30", ts=1001.0)
    c.save(path)

    c2 = CallsignCache()
    c2.load(path)
    assert len(c2) == 2
    assert c2.lookup("KF9UG", ts=1000.0) == "EN71"
    assert c2.entry("KE2CUR").count == 1


# --- pipeline -------------------------------------------------------------

def _decode_and_classify(line):
    d = parse_line(line)
    return d, classify(d.message)


def test_pipeline_direct_from_message_grid():
    c = CallsignCache()
    d, m = _decode_and_classify("023915  -1 173  1.33  327.5 CQ KF9UG  EN71")
    obs = process_decode(d, m, monitor_grid="FN19", cache=c, ts=1000.0)
    assert [o.kind for o in obs] == ["direct"]
    assert obs[0].tx_call == "KF9UG" and obs[0].tx_grid == "EN71"
    # the decode also taught the cache KF9UG's grid
    assert c.lookup("KF9UG", ts=1000.0) == "EN71"


def test_pipeline_indirect_after_learning_both_grids():
    c = CallsignCache()
    # Earlier slots teach both stations' grids via their CQs.
    for line in ("023600  -1 173 1.30  500.0 CQ KE2CUR FN30",
                 "023600  -1 173 1.30  600.0 CQ KF9UG  EN71"):
        d, m = _decode_and_classify(line)
        process_decode(d, m, monitor_grid="FN19", cache=c, ts=1000.0)

    # KF9UG transmits, reporting it heard KE2CUR at -02.
    d, m = _decode_and_classify("023645  -7 147  1.34  327.6 KE2CUR KF9UG  -02")
    obs = process_decode(d, m, monitor_grid="FN19", cache=c, ts=1001.0)

    kinds = {o.kind for o in obs}
    assert kinds == {"direct", "indirect"}

    direct = next(o for o in obs if o.kind == "direct")
    assert direct.tx_call == "KF9UG" and direct.snr_db == -7   # we heard KF9UG

    indirect = next(o for o in obs if o.kind == "indirect")
    # TX = call_to (KE2CUR), RX = call_de (KF9UG)
    assert indirect.tx_call == "KE2CUR" and indirect.tx_grid == "FN30"
    assert indirect.rx_call == "KF9UG" and indirect.rx_grid == "EN71"
    assert indirect.snr_db == -2
