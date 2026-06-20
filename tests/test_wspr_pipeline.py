"""Tests for the WSPR monitor pipeline (line -> observation/freq pairs)."""

from watchersattherim.monitor.wspr_pipeline import ingest


def test_ingest_decode_line():
    out = ingest("091800  -9  1.1  14.097046  0  ND6P FN19 30", monitor_grid="FN20")
    assert out is not None and len(out) == 1
    obs, freq_hz = out[0]
    assert obs.kind == "direct"
    assert obs.tx_grid == "FN19" and obs.power_dbm == 30
    assert obs.rx_grid == "FN20" and obs.snr_db == -9
    assert freq_hz == 14_097_046       # absolute, straight from the decode


def test_ingest_non_decode_returns_none():
    # cycle header and noise are not decodes
    assert ingest("09:18:00 decodes: 0", monitor_grid="FN20") is None
    assert ingest("<DecodeFinished>", monitor_grid="FN20") is None


def test_ingest_gridless_decode_is_empty():
    # a grid-less (type-2) message is a decode, but yields nothing to store
    out = ingest("091800  -9  1.1  14.097046  0  PJ4/K1ABC 37", monitor_grid="FN20")
    assert out == []
