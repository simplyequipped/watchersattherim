"""Tests for wsprmon line parsing and WSPR message classification."""

import pytest

from watchersattherim.monitor.wspr_parser import (
    classify,
    extract,
    parse_line,
    parse_stream,
)


# --- line parsing ---------------------------------------------------------

def test_parse_decode_line():
    d = parse_line("091800  -9  1.1  14.097046  0  ND6P FN19 30")
    assert d is not None
    assert d.hhmmss == "091800"
    assert d.snr == -9
    assert d.dt == 1.1
    assert d.freq_mhz == 14.097046
    assert d.drift == 0
    assert d.message == "ND6P FN19 30"


def test_parse_negative_dt_and_drift():
    d = parse_line("091800  -1 -0.8  14.097103 -1  NM7J FN20 30")
    assert d is not None and d.dt == -0.8 and d.snr == -1 and d.drift == -1


@pytest.mark.parametrize("line", [
    "09:18:00 decodes: 9",          # cycle header (colons, not 6 digits)
    "./wsprmon -card 2 0 -f 14.0956",
    "ALSA lib pcm_dmix.c:1000:(snd_pcm_dmix_open) unable to open slave",
    "<DecodeFinished>",
    "",
])
def test_non_decode_lines_skipped(line):
    assert parse_line(line) is None


def test_parse_stream_filters():
    lines = [
        "091800  -9  1.1  14.097046  0  ND6P FN19 30",
        "09:18:00 decodes: 1",
        "garbage",
    ]
    out = list(parse_stream(iter(lines)))
    assert len(out) == 1 and out[0].message == "ND6P FN19 30"


# --- classification -------------------------------------------------------

def test_type1_standard_kept():
    s = classify("ND6P FN19 30")
    assert s is not None
    assert s.call == "ND6P" and s.grid == "FN19" and s.power_dbm == 30
    assert s.hashed is False


def test_type1_six_char_grid():
    s = classify("W3HH FN20AB 30")
    assert s is not None and s.grid == "FN20AB"


def test_type3_resolved_hash_stripped():
    s = classify("<PJ4/K1ABC> FN52UD 37")
    assert s is not None
    assert s.call == "PJ4/K1ABC"      # angle brackets stripped
    assert s.grid == "FN52UD" and s.power_dbm == 37 and s.hashed is True


def test_type3_unknown_hash_keeps_grid():
    s = classify("<...> FN52UD 37")
    assert s is not None
    assert s.call is None and s.hashed is True     # unknown call, grid kept
    assert s.grid == "FN52UD" and s.power_dbm == 37


def test_type2_no_grid_dropped():
    # compound-call station advertising its call with no grid
    assert classify("PJ4/K1ABC 37") is None


@pytest.mark.parametrize("message", [
    "",
    "JUST ONE",
    "NOTAGRID THING 30",     # grid slot isn't a Maidenhead locator
    "ND6P FN19 notpower",    # power isn't an integer
])
def test_junk_dropped(message):
    assert classify(message) is None


# --- extraction -----------------------------------------------------------

def test_extract_direct_with_power():
    spot = classify("ND6P FN19 30")
    obs = extract(spot, decode_snr=-9, monitor_grid="FN20", monitor_call="W1MON")
    assert len(obs) == 1
    o = obs[0]
    assert o.kind == "direct"
    assert o.tx_grid == "FN19" and o.tx_call == "ND6P"
    assert o.rx_grid == "FN20" and o.rx_call == "W1MON"
    assert o.snr_db == -9 and o.power_dbm == 30


def test_extract_unknown_call_keeps_grid_and_power():
    spot = classify("<...> FN52UD 37")
    obs = extract(spot, decode_snr=-20, monitor_grid="FN20")
    assert len(obs) == 1
    assert obs[0].tx_call is None
    assert obs[0].tx_grid == "FN52UD" and obs[0].power_dbm == 37
