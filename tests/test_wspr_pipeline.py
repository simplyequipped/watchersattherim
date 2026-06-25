"""Tests for the WSPR monitor pipeline (line -> observation/freq pairs)."""

from watchersattherim.monitor.wspr_pipeline import ingest


def test_ingest_decode_line():
    # offset mode: freq column is the audio offset in Hz, dial is added by ingest
    out = ingest("091800  -9  1.1  1446.0  0  ND6P FN19 30", 7_038_600, monitor_grid="FN20")
    assert out is not None and len(out) == 1
    obs, freq_hz = out[0]
    assert obs.kind == "direct"
    assert obs.tx_grid == "FN19" and obs.power_dbm == 30
    assert obs.rx_grid == "FN20" and obs.snr_db == -9
    assert freq_hz == 7_040_046         # dial 7038600 + 1446 Hz offset


def test_ingest_non_decode_returns_none():
    # cycle header and noise are not decodes
    assert ingest("09:18:00 decodes: 0", 7_038_600, monitor_grid="FN20") is None
    assert ingest("<DecodeFinished>", 7_038_600, monitor_grid="FN20") is None


def test_ingest_gridless_decode_is_empty():
    # a grid-less (type-2) message is a decode, but yields nothing to store
    out = ingest("091800  -9  1.1  1446.0  0  PJ4/K1ABC 37", 7_038_600, monitor_grid="FN20")
    assert out == []
