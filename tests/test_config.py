"""Tests for INI config loading, defaults, validation, and receiver argv."""

import os
from pathlib import Path

import pytest

from watchersattherim.monitor.config import ConfigError, load, loads, parse_duration

REPO = Path(__file__).resolve().parent.parent

MINIMAL = """
[monitor]
grid = FN19

[receiver:20m]
freq = 14074000
card = 8

[collector]
address = abc123def456
"""


# --- duration -------------------------------------------------------------

@pytest.mark.parametrize("text,secs", [
    ("2h", 7200), ("120m", 7200), ("1d", 86400), ("90s", 90), ("90", 90),
])
def test_parse_duration(text, secs):
    assert parse_duration(text) == secs


def test_parse_duration_int_passthrough():
    assert parse_duration(7200) == 7200


def test_parse_duration_bad():
    with pytest.raises(ConfigError):
        parse_duration("2 hours")


# --- minimal load + defaults ---------------------------------------------

def test_minimal_applies_defaults():
    c = loads(MINIMAL)
    assert c.monitor.grid == "FN19"
    assert round(c.monitor.lat, 1) == 49.5 and round(c.monitor.lon, 1) == -77.0
    assert c.ft8mon_path == "ft8mon"
    assert c.observations.callsigns is False and c.observations.indirect is True
    assert c.cache.enabled is True and c.cache.persist is False
    assert c.cache.max_entries == 10000 and c.cache.ttl_sec == 7200
    assert c.collector.address == "abc123def456"
    assert c.collector.send_interval == 60 and c.collector.delivery == "direct"
    assert c.collector.send_empty_batches is False
    assert c.restart_after_silent_cycles == 0
    assert c.storage.dir == "~/.watchersattherim"


# --- receiver argv --------------------------------------------------------

def _one_receiver(body):
    return loads(f"""
[monitor]
grid = FN19
[collector]
address = d
{body}
""").receivers[0]


def test_audio_receiver_default_channel():
    r = _one_receiver("[receiver:20m]\nfreq = 14074000\ncard = 8")
    assert r.kind == "audio" and r.band == "20m"
    assert r.ft8mon_args() == ["-card", "8", "0"]


def test_audio_receiver_explicit_channel():
    r = _one_receiver("[receiver:20m]\nfreq = 14074000\ncard = 8:1")
    assert r.channel == 1 and r.ft8mon_args() == ["-card", "8", "1"]


def test_sdr_sdrip_builds_ip_mhz():
    r = _one_receiver(
        "[receiver:40m]\nfreq = 7074000\ninput = sdrip\nip = 192.168.3.100"
    )
    assert r.kind == "sdr"
    assert r.ft8mon_args() == ["-card", "sdrip", "192.168.3.100,7.074"]


def test_sdr_airspy_serial_and_default():
    r = _one_receiver("[receiver:20m]\nfreq = 14074000\ninput = airspy\nserial = AB12")
    assert r.ft8mon_args() == ["-card", "airspy", "AB12,14.074"]
    r2 = _one_receiver("[receiver:20m]\nfreq = 14074000\ninput = airspy")
    assert r2.ft8mon_args() == ["-card", "airspy", ",14.074"]


def test_file_receiver():
    r = _one_receiver("[receiver:20m]\nfreq = 14074000\npath = /tmp/x.wav")
    assert r.kind == "file"
    assert r.ft8mon_args() == ["-card", "file", "/tmp/x.wav"]


def test_file_receiver_expands_tilde():
    r = _one_receiver("[receiver:20m]\nfreq = 14074000\npath = ~/x.wav")
    assert r.path == os.path.expanduser("~/x.wav")
    assert "~" not in r.ft8mon_args()[2]


def test_args_appended():
    r = _one_receiver(
        "[receiver:40m]\nfreq = 7074000\ninput = sdrip\nip = 10.0.0.1\nargs = -only 1500"
    )
    assert r.ft8mon_args() == ["-card", "sdrip", "10.0.0.1,7.074", "-only", "1500"]


# --- validation -----------------------------------------------------------

@pytest.mark.parametrize("body,msg", [
    ("[receiver:20m]\nfreq=1\ncard=8\n[collector]\naddress=d", "grid"),
    ("[monitor]\ngrid=FN19\n[collector]\naddress=d", "receiver"),
    ("[monitor]\ngrid=FN19\n[receiver:20m]\nfreq=1\ncard=8", "address"),
])
def test_missing_required(body, msg):
    with pytest.raises(ConfigError, match=msg):
        loads(body)


def test_receiver_needs_exactly_one_input():
    with pytest.raises(ConfigError, match="exactly one"):
        _one_receiver("[receiver:20m]\nfreq=1\ncard=8\ninput=airspy")


def test_receiver_freq_required():
    with pytest.raises(ConfigError, match="freq"):
        _one_receiver("[receiver:20m]\ncard=8")


def test_sdrip_requires_ip():
    with pytest.raises(ConfigError, match="requires ip"):
        _one_receiver("[receiver:40m]\nfreq=7074000\ninput=sdrip")


def test_propagated_requires_node():
    with pytest.raises(ConfigError, match="propagation_node"):
        loads(MINIMAL + "\ndelivery = propagated\n")


# --- shipped example files ------------------------------------------------

def test_minimal_example_loads():
    c = load(str(REPO / "examples/monitor.minimal.example.ini"))
    assert c.monitor.grid == "FN19"
    assert len(c.receivers) == 1 and c.receivers[0].kind == "audio"
    # everything else defaulted
    assert c.cache.persist is False and c.cache.ttl_sec == 7200
    assert c.collector.delivery == "direct" and c.ft8mon_path == "ft8mon"
    assert c.storage.dir == "~/.watchersattherim"


def test_full_example_exercises_every_option():
    c = load(str(REPO / "examples/monitor.full.example.ini"))

    assert c.monitor.lat == 49.51 and c.monitor.lon == -77.02   # overrides
    assert c.ft8mon_path == "/usr/local/bin/ft8mon"

    by_band = {r.band: r for r in c.receivers}
    assert set(by_band) == {"20m", "40m", "30m"}
    assert by_band["20m"].ft8mon_args() == ["-card", "8", "1"]
    assert by_band["40m"].ft8mon_args() == ["-card", "sdrip", "192.168.3.100,7.074"]
    assert by_band["30m"].ft8mon_args() == ["-card", "airspy", "AB12CD34,10.136"]

    assert c.observations.callsigns is True and c.observations.indirect is False
    assert c.cache.max_entries == 20000 and c.cache.ttl_sec == 14400
    assert c.cache.persist is True
    assert c.collector.delivery == "propagated"
    assert c.collector.propagation_node == "fedcba987654"
    assert c.collector.send_interval == 120
    assert c.collector.max_pending_observations == 25000
    assert c.collector.send_empty_batches is True
    assert c.restart_after_silent_cycles == 5
    assert c.reticulum.config_dir == "~/.reticulum"
    assert c.storage.dir == "/var/lib/watchersattherim"
