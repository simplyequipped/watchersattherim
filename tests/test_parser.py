"""Tests for line parsing and message classification against real ft8mon output."""

from pathlib import Path

import pytest

from watchersattherim.monitor.observations import extract, grid_to_latlon
from watchersattherim.monitor.parser import Kind, classify, parse_line

SAMPLE = Path(__file__).resolve().parent.parent / "docs/samples/ft8mon_output.txt"


# --- line parsing ---------------------------------------------------------

def test_parse_decode_line():
    d = parse_line("023645  -7 147  1.34  327.6 KE2CUR KF9UG  -02")
    assert d is not None
    assert d.hhmmss == "023645"
    assert d.snr == -7
    assert d.bits == 147
    assert d.dt == 1.34
    assert d.freq == 327.6
    assert d.message == "KE2CUR KF9UG  -02".strip()


def test_parse_negative_dt():
    d = parse_line("023700 -13 161 -0.19 1889.6 VA6RCN WX5LOK DN40")
    assert d is not None and d.dt == -0.19 and d.snr == -13


@pytest.mark.parametrize("line", [
    "./ft8mon -card 8 0",
    "ALSA lib pcm_dsnoop.c:572:(snd_pcm_dsnoop_open) [error.pcm] unable to open slave",
    "card=8: requested rate 12000 unsupported, using 48000",
    "02:37:00 decodes: 17",
    "",
])
def test_non_decode_lines_skipped(line):
    assert parse_line(line) is None


# --- classification -------------------------------------------------------

def test_standard_with_grid():
    m = classify("N1LYP  N5IIT FN19")
    assert m.kind is Kind.STANDARD
    assert m.call_to == "N1LYP" and m.call_de == "N5IIT"
    assert m.grid == "FN19" and m.report_db is None


def test_standard_with_report():
    m = classify("KE2CUR KF9UG  -02")
    assert m.kind is Kind.STANDARD
    assert m.call_to == "KE2CUR" and m.call_de == "KF9UG"
    assert m.report_db == -2 and m.grid is None


def test_rr73_is_ack_not_grid():
    # RR73 matches the locator pattern but is the sign-off, not a transmitter grid.
    m = classify("W3GO K1ABC RR73")
    assert m.kind is Kind.STANDARD
    assert m.grid is None and m.report_db is None


def test_report_with_r_prefix():
    m = classify("N4DWD KR1ATC RRR")
    assert m.report_db is None  # RRR is an ack, not an SNR
    m2 = classify("W3GO  KQ4EPK R+08")
    assert m2.report_db == 8


def test_cq_plain():
    m = classify("CQ KF9UG  EN71")
    assert m.kind is Kind.CQ
    assert m.call_de == "KF9UG" and m.grid == "EN71"
    assert m.cq_modifier is None and m.call_to is None


def test_cq_with_modifier():
    m = classify("CQ POTA KF0MSJ EM48")
    assert m.kind is Kind.CQ
    assert m.cq_modifier == "POTA"
    assert m.call_de == "KF0MSJ" and m.grid == "EM48"


def test_cq_dx_no_grid():
    m = classify("CQ DX W1HAT")
    assert m.kind is Kind.CQ and m.cq_modifier == "DX"
    assert m.call_de == "W1HAT" and m.grid is None


def test_portable_call():
    m = classify("VE6AGD      W1AW/7 -11")
    assert m.kind is Kind.STANDARD
    assert m.call_de == "W1AW/7" and m.report_db == -11


def test_hashed_call_marks_nonstd():
    m = classify("<...22> KA2BSK R-05")
    assert m.kind is Kind.NONSTD
    assert m.to_hashed is True and m.call_to is None
    assert m.call_de == "KA2BSK" and m.report_db == -5


@pytest.mark.parametrize("text", [
    "i3=0 n3=1",
    "i3=0 n3=6",
    "B+DZ+VY+FSZI",
    "TU; 156QAJ 4T6DQP 559 8174",
    "C",
])
def test_rejects(text):
    assert classify(text).kind is Kind.REJECT


# --- maidenhead -----------------------------------------------------------

def test_grid_to_latlon_fn19():
    lat, lon = grid_to_latlon("FN19")
    assert lat == pytest.approx(49.5, abs=0.01)
    assert lon == pytest.approx(-77.0, abs=0.01)


# --- observation extraction ----------------------------------------------

def test_direct_from_cq_grid():
    m = classify("CQ KF9UG  EN71")
    obs = extract(m, decode_snr=-3, monitor_grid="FN19", monitor_call="W1MON")
    assert len(obs) == 1
    o = obs[0]
    assert o.kind == "direct"
    assert o.tx_call == "KF9UG" and o.tx_grid == "EN71"
    assert o.rx_grid == "FN19" and o.snr_db == -3


def test_indirect_needs_both_in_cache():
    m = classify("KE2CUR KF9UG  -02")  # KF9UG reports hearing KE2CUR at -02
    cache = {"KE2CUR": "FN30", "KF9UG": "EN71"}
    obs = extract(m, decode_snr=-7, monitor_grid="FN19",
                  lookup=lambda c: cache.get(c))
    kinds = {o.kind for o in obs}
    assert "indirect" in kinds
    ind = next(o for o in obs if o.kind == "indirect")
    # TX = call_to (KE2CUR), RX = call_de (KF9UG)
    assert ind.tx_call == "KE2CUR" and ind.rx_call == "KF9UG"
    assert ind.snr_db == -2


def test_no_observation_without_grid_or_cache():
    m = classify("KE2CUR KF9UG  -02")
    assert extract(m, decode_snr=-7, monitor_grid="FN19") == []


# --- whole-sample sanity --------------------------------------------------

def test_sample_classifies_cleanly():
    decodes = 0
    rejects = 0
    for line in SAMPLE.read_text().splitlines():
        d = parse_line(line)
        if d is None:
            continue
        decodes += 1
        if classify(d.message).kind is Kind.REJECT:
            rejects += 1
    assert decodes > 600
    # Rejects are the genuine non-standard/corrupt decodes; should be a small
    # fraction of a busy band capture.
    assert rejects / decodes < 0.05
