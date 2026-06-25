"""Tests for INI config loading, defaults, validation, and receiver argv."""

import os
from pathlib import Path

import pytest

from watchersattherim.monitor.config import (
    Blacklist, ConfigError, load, loads, parse_duration, sdrfanout_argv,
)

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
    assert c.collector.send_interval == 120 and c.collector.delivery == "direct"
    assert c.collector.send_empty_batches is False
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
    assert r.kind == "backend"
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


# --- shared SDR (sdr = yes / [sdr]) ---------------------------------------

def _sdr_cfg(receivers, sdr_body="driver = hackrf\n", storage="/var/lib/watr"):
    return loads(
        "[monitor]\ngrid = FN19\n[collector]\naddress = d\n"
        f"[storage]\ndir = {storage}\n"
        f"[sdr]\n{sdr_body}"
        f"{receivers}"
    )


def test_stream_receiver_builds_card_stream():
    c = _sdr_cfg("[receiver:40m-ft8]\nfreq = 7074000\nsdr = yes\n")
    r = c.receivers[0]
    assert r.kind == "sdr"
    assert r.path == "/var/lib/watr/run/40m-ft8.fifo"
    assert r.ft8mon_args() == ["-card", "stream", "/var/lib/watr/run/40m-ft8.fifo"]


def test_stream_wspr_receiver_args():
    c = _sdr_cfg("[receiver:40m-wspr]\nmode = wspr\nfreq = 7038600\nsdr = yes\n")
    r = c.receivers[0]
    assert r.wsprmon_args() == [
        "-hz", "-card", "stream", "/var/lib/watr/run/40m-wspr.fifo"
    ]


def test_sdr_section_load_and_defaults():
    c = _sdr_cfg(
        "[receiver:x]\nfreq = 7074000\nsdr = yes\n",
        sdr_body="driver = hackrf\ngain = 40\nppm = -1.4\n",
        storage="/v",
    )
    s = c.sdr
    assert s.driver == "hackrf" and s.gain == "40" and s.ppm == -1.4
    assert s.guard == 10000 and s.buffer == 1.0 and s.path == "sdrfanout"
    assert s.rate is None and s.center is None and s.antenna is None
    assert s.runtime_dir == "/v/run"


def test_sdr_runtime_dir_override():
    c = _sdr_cfg(
        "[receiver:x]\nfreq = 7074000\nsdr = yes\n",
        sdr_body="runtime_dir = /run/watr\n",
    )
    assert c.sdr.runtime_dir == "/run/watr"
    assert c.receivers[0].path == "/run/watr/x.fifo"


def test_no_sdr_section_is_none():
    assert loads(MINIMAL).sdr is None


def test_stream_requires_sdr_section():
    with pytest.raises(ConfigError, match="requires an"):
        loads(
            "[monitor]\ngrid = FN19\n[collector]\naddress = d\n"
            "[receiver:x]\nfreq = 7074000\nsdr = yes\n"
        )


def test_card_and_sdr_conflict():
    with pytest.raises(ConfigError, match="exactly one"):
        _sdr_cfg("[receiver:x]\nfreq = 7074000\ncard = 8\nsdr = yes\n")


def test_sdr_named_value_errors():
    with pytest.raises(ConfigError, match="yes/true"):
        _sdr_cfg("[receiver:x]\nfreq = 7074000\nsdr = rx0\n")


def test_sdr_no_is_not_selected():
    # sdr = no must not count as a source (else it'd be "none selected")
    with pytest.raises(ConfigError, match="exactly one"):
        _sdr_cfg("[receiver:x]\nfreq = 7074000\nsdr = no\n")


def test_sdrfanout_argv_channel_plan():
    c = _sdr_cfg(
        "[receiver:40m-wspr]\nmode = wspr\nfreq = 7038600\nsdr = yes\n"
        "[receiver:40m-ft8]\nfreq = 7074000\nsdr = yes\n",
        sdr_body="driver = hackrf\ngain = 40\nppm = -1.4\n",
        storage="/v",
    )
    streams = [r for r in c.receivers if r.kind == "sdr"]
    # driver passes through verbatim (sdrfanout turns a bare name into driver=<name>)
    assert sdrfanout_argv(c.sdr, streams) == [
        "sdrfanout", "-driver", "hackrf", "-gain", "40",
        "-guard", "10000", "-ppm", "-1.4", "-buffer", "1.0",
        "-ch", "7038600:/v/run/40m-wspr.fifo",
        "-ch", "7074000:/v/run/40m-ft8.fifo",
    ]


def test_sdrfanout_argv_arbitrary_channel_count():
    # nothing assumes 2 channels: N receivers -> N distinct FIFOs + N -ch entries.
    c = _sdr_cfg(
        "[receiver:a]\nfreq = 7038600\nsdr = yes\n"
        "[receiver:b]\nfreq = 7074000\nsdr = yes\n"
        "[receiver:c]\nmode = wspr\nband = 30m\nfreq = 10138700\nsdr = yes\n",
        storage="/v",
    )
    streams = [r for r in c.receivers if r.kind == "sdr"]
    argv = sdrfanout_argv(c.sdr, streams)
    chs = [argv[i + 1] for i, a in enumerate(argv) if a == "-ch"]
    assert chs == [
        "7038600:/v/run/a.fifo",
        "7074000:/v/run/b.fifo",
        "10138700:/v/run/c.fifo",
    ]
    assert len({r.path for r in streams}) == 3      # distinct FIFOs


def test_sdrfanout_argv_omits_auto_fields():
    c = _sdr_cfg("[receiver:x]\nfreq = 7074000\nsdr = yes\n", sdr_body="")
    argv = sdrfanout_argv(c.sdr, c.receivers)
    for flag in ("-driver", "-gain", "-rate", "-center", "-ppm", "-antenna"):
        assert flag not in argv
    assert argv[0] == "sdrfanout"
    assert argv[-2:] == ["-ch", f"7074000:{c.receivers[0].path}"]


# --- blacklist ------------------------------------------------------------

def test_no_blacklist_section_blocks_nothing():
    bl = loads(MINIMAL).blacklist
    assert bl == Blacklist()
    assert not bl.blocks("RF73", "ZL1ABC", 7076000)


def _blacklist_cfg(body: str) -> Blacklist:
    return loads(MINIMAL + "\n[blacklist]\n" + body).blacklist


def test_blacklist_parses_grids_and_calls_uppercased():
    bl = _blacklist_cfg("grids = rf73, QF55\ncallsigns = zl1abc\n")
    assert bl.grids == {"RF73", "QF55"}
    assert bl.calls == {"ZL1ABC"}


def test_blacklist_grid_match_is_case_insensitive():
    bl = _blacklist_cfg("grids = RF73\n")
    assert bl.blocks("rf73", None, 7076000)
    assert not bl.blocks("FN42", None, 7076000)


def test_blacklist_callsign_match():
    bl = _blacklist_cfg("callsigns = N0CALL\n")
    assert bl.blocks("FN42", "n0call", 7076000)
    assert not bl.blocks("FN42", "W1AW", 7076000)


def test_blacklist_freq_range_and_single_tolerance():
    bl = _blacklist_cfg("freqs = 7075120-7075130, 14074500\n")
    assert bl.blocks("", None, 7075125)            # inside the range
    assert not bl.blocks("", None, 7075131)        # just past the range
    assert bl.blocks("", None, 14074495)           # within +/-10 Hz of the single value
    assert not bl.blocks("", None, 14074520)       # outside the tolerance window


def test_blacklist_freqs_newline_separated():
    bl = _blacklist_cfg("freqs =\n    7075120-7075130\n    14074500\n")
    assert len(bl.freqs) == 2


def test_blacklist_bad_freq_errors():
    with pytest.raises(ConfigError, match="invalid entry"):
        _blacklist_cfg("freqs = not-a-number\n")


# --- mode / band / wsprmon ------------------------------------------------

def test_mode_defaults_to_ft8():
    r = _one_receiver("[receiver:20m]\nfreq = 14074000\ncard = 8")
    assert r.mode == "ft8" and r.name == "20m" and r.band == "20m"


def test_wspr_mode_and_band_override():
    r = _one_receiver(
        "[receiver:40m-wspr]\nmode = wspr\nband = 40m\nfreq = 7038600\ncard = 2"
    )
    assert r.mode == "wspr" and r.name == "40m-wspr" and r.band == "40m"


def test_wsprmon_args_audio():
    r = _one_receiver("[receiver:40m-wspr]\nmode = wspr\nfreq = 7038600\ncard = 2:0")
    assert r.wsprmon_args() == ["-hz", "-card", "2", "0"]
    assert r.wsprmon_args("/opt/wsprd") == [
        "-wsprd", "/opt/wsprd", "-hz", "-card", "2", "0"
    ]


def test_wsprmon_args_file_puts_file_last():
    r = _one_receiver("[receiver:w]\nmode = wspr\nfreq = 7038600\npath = /tmp/x.wav")
    assert r.wsprmon_args() == ["-hz", "-file", "/tmp/x.wav"]


def test_wsprmon_args_workdir():
    r = _one_receiver("[receiver:40m-wspr]\nmode = wspr\nfreq = 7038600\ncard = 2:0")
    assert r.wsprmon_args("/opt/wsprd", "/var/wd") == [
        "-wsprd", "/opt/wsprd", "-a", "/var/wd", "-hz", "-card", "2", "0"
    ]


def test_wsprmon_args_workdir_before_file():
    # -a must precede -file, which consumes the rest of argv
    r = _one_receiver("[receiver:w]\nmode = wspr\nfreq = 7038600\npath = /tmp/x.wav")
    assert r.wsprmon_args(workdir="/var/wd") == [
        "-a", "/var/wd", "-hz", "-file", "/tmp/x.wav"
    ]


def test_wsprmon_and_wsprd_paths():
    c = loads(
        "[monitor]\ngrid = FN19\n[collector]\naddress = d\n"
        "[receiver:20m]\nfreq = 14074000\ncard = 8\n"
        "[wsprmon]\npath = /opt/wsprmon\nwsprd_path = /opt/wsprd\n"
    )
    assert c.wsprmon_path == "/opt/wsprmon" and c.wsprd_path == "/opt/wsprd"


def test_wsprmon_paths_default():
    c = loads(MINIMAL)
    assert c.wsprmon_path == "wsprmon" and c.wsprd_path is None


def test_monitor_debug_default_and_set():
    assert loads(MINIMAL).monitor.debug is False
    c = loads("[monitor]\ngrid = FN19\ndebug = true\n"
              "[receiver:20m]\nfreq = 14074000\ncard = 8\n[collector]\naddress = d\n")
    assert c.monitor.debug is True


def test_min_decode_snr_defaults_by_mode_and_override():
    assert _one_receiver("[receiver:20m]\nfreq=14074000\ncard=8").min_decode_snr == -25
    assert _one_receiver(
        "[receiver:w]\nmode=wspr\nfreq=7038600\ncard=2").min_decode_snr == -30
    assert _one_receiver(
        "[receiver:20m]\nfreq=14074000\ncard=8\nmin_decode_snr=-18").min_decode_snr == -18


def test_restart_after_silent_default_and_duration():
    assert _one_receiver(
        "[receiver:20m]\nfreq=14074000\ncard=8").restart_after_silent_sec == 0
    assert _one_receiver(
        "[receiver:20m]\nfreq=14074000\ncard=8\nrestart_after_silent=5m"
    ).restart_after_silent_sec == 300


def test_enabled_false_skips_receiver():
    c = loads(
        "[monitor]\ngrid = FN19\n[collector]\naddress = d\n"
        "[receiver:20m]\nfreq = 14074000\ncard = 8\n"
        "[receiver:40m-wspr]\nenabled = false\nmode = wspr\nfreq = 7038600\ncard = 2\n"
    )
    assert [r.name for r in c.receivers] == ["20m"]


# --- validation -----------------------------------------------------------


def test_all_receivers_disabled_errors():
    with pytest.raises(ConfigError, match="enabled"):
        loads(
            "[monitor]\ngrid = FN19\n[collector]\naddress = d\n"
            "[receiver:20m]\nenabled = false\nfreq = 14074000\ncard = 8\n"
        )


def test_bad_mode_errors():
    with pytest.raises(ConfigError, match="mode"):
        _one_receiver("[receiver:20m]\nmode = jt65\nfreq = 1\ncard = 8")

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


# Every option set to a non-default value, so the loader is exercised end to end.
# (The shipped example is a usable template, tested separately below.)
_EVERY_OPTION = """
[monitor]
grid = FN19
lat = 49.51
lon = -77.02
[ft8mon]
path = /usr/local/bin/ft8mon
[wsprmon]
path = /usr/local/bin/wsprmon
wsprd_path = /usr/local/bin/wsprd
[receiver:20m]
freq = 14074000
card = 8:1
min_decode_snr = -22
restart_after_silent = 5m
[receiver:40m]
freq = 7074000
input = sdrip
ip = 192.168.3.100
[receiver:40m-wspr]
mode = wspr
band = 40m
freq = 7038600
input = sdrip
ip = 192.168.3.100
[receiver:30m]
freq = 10136000
input = airspy
serial = AB12CD34
[observations]
callsigns = true
indirect = false
[cache]
enabled = true
max_entries = 20000
ttl = 4h
persist = true
[collector]
address = abc123def456
send_interval = 120
delivery = propagated
propagation_node = fedcba987654
max_pending_observations = 25000
send_empty_batches = true
[reticulum]
config_dir = ~/.reticulum
[storage]
dir = /var/lib/watchersattherim
"""


def test_every_monitor_option_parses():
    c = loads(_EVERY_OPTION)
    assert c.monitor.lat == 49.51 and c.monitor.lon == -77.02   # overrides
    assert c.ft8mon_path == "/usr/local/bin/ft8mon"
    assert c.wsprmon_path == "/usr/local/bin/wsprmon"
    assert c.wsprd_path == "/usr/local/bin/wsprd"

    by_name = {r.name: r for r in c.receivers}
    assert set(by_name) == {"20m", "40m", "40m-wspr", "30m"}
    assert by_name["20m"].ft8mon_args() == ["-card", "8", "1"]
    assert by_name["20m"].min_decode_snr == -22
    assert by_name["20m"].restart_after_silent_sec == 300
    assert by_name["40m"].ft8mon_args() == ["-card", "sdrip", "192.168.3.100,7.074"]
    assert by_name["40m-wspr"].mode == "wspr" and by_name["40m-wspr"].band == "40m"
    assert by_name["30m"].ft8mon_args() == ["-card", "airspy", "AB12CD34,10.136"]

    assert c.observations.callsigns is True and c.observations.indirect is False
    assert c.cache.max_entries == 20000 and c.cache.ttl_sec == 14400
    assert c.cache.persist is True
    assert c.collector.delivery == "propagated"
    assert c.collector.propagation_node == "fedcba987654"
    assert c.collector.send_interval == 120
    assert c.collector.max_pending_observations == 25000
    assert c.collector.send_empty_batches is True
    assert c.reticulum.config_dir == "~/.reticulum"
    assert c.storage.dir == "/var/lib/watchersattherim"


def test_shipped_monitor_example_is_usable():
    # the example must load and run as-is: required active, optional at default
    c = load(str(REPO / "examples/monitor.full.example.ini"))
    assert c.monitor.grid == "FN19"
    assert [(r.name, r.mode) for r in c.receivers] == [("20m", "ft8")]   # one enabled
    assert c.collector.delivery == "direct"                              # safe default
    assert c.ft8mon_path == "ft8mon" and c.wsprmon_path == "wsprmon"     # found on PATH
    assert c.wsprd_path is None
