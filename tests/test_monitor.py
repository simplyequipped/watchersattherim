"""Tests for the Monitor orchestration (handle_line + flush), using fakes."""

from watchersattherim.monitor.config import load
from watchersattherim.monitor.monitor import Monitor


def make_config(tmp_path, extra="", receiver_extra=""):
    ini = f"""
[monitor]
grid = FN19
[receiver:20m]
freq = 14074000
card = 8
{receiver_extra}
[collector]
address = abc123
{extra}
"""
    p = tmp_path / "monitor.ini"
    p.write_text(ini)
    return load(str(p))


class RecordingSender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, batch):
        if self.ok:
            self.sent.append(batch)
            return True
        return False


def fixed_clock(t=1000.0):
    return lambda: t


def test_handle_decode_and_flush(tmp_path):
    cfg = make_config(tmp_path)
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]

    # CQ with a grid -> one direct observation
    mon.handle_line(r, "024015   0 174  1.31 2185.1 CQ  W3GO  FN20")
    assert mon.flush() is True

    batch = sender.sent[0]
    assert batch["monitor"]["grid"] == "FN19"
    assert batch["stats"]["decodes_seen"] == 1
    obs = batch["observations"]
    assert len(obs) == 1 and obs[0]["type"] == "direct"
    assert obs[0]["band"] == "20m"
    assert obs[0]["freq"] == 14074000 + 2185      # dial + audio offset (rounded)
    assert obs[0]["tx_grid"] == "FN20"


def test_handle_wspr_decode_and_flush(tmp_path):
    cfg = make_config(tmp_path, extra=(
        "[receiver:40m-wspr]\n"
        "mode = wspr\n"
        "band = 40m\n"
        "freq = 7038600\n"
        "card = 2\n"
    ))
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = next(rc for rc in cfg.receivers if rc.mode == "wspr")

    mon.handle_line(r, "091800  -9  1.1  1446.0  0  ND6P FN20 30")
    assert mon.flush() is True

    obs = [o for o in sender.sent[0]["observations"] if o["mode"] == "WSPR"]
    assert len(obs) == 1
    o = obs[0]
    assert o["band"] == "40m" and o["type"] == "direct"
    assert o["tx_grid"] == "FN20" and o["snr"] == -9
    assert o["power_dbm"] == 30
    assert o["freq"] == 7_040_046          # dial 7038600 + 1446 Hz audio offset


def test_non_decode_lines_ignored(tmp_path):
    # send_empty_batches so the (empty) batch is still transmitted and inspectable
    cfg = make_config(tmp_path, extra="send_empty_batches = true")
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]
    mon.handle_line(r, "02:37:00 decodes: 17")     # cycle header
    mon.handle_line(r, "ALSA lib pcm.c: blah")     # noise
    mon.flush()
    batch = sender.sent[0]
    assert batch["stats"]["decodes_seen"] == 0
    assert batch["observations"] == []


def test_indirect_emitted_when_enabled(tmp_path):
    cfg = make_config(tmp_path)
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]
    # learn both stations' grids, then overhear a report exchange
    mon.handle_line(r, "024000  -1 173  1.30  500.0 CQ KE2CUR FN30")
    mon.handle_line(r, "024000  -1 173  1.30  600.0 CQ KF9UG  EN71")
    mon.handle_line(r, "024015  -7 147  1.34  327.6 KE2CUR KF9UG  -02")
    mon.flush()
    kinds = {o["type"] for o in sender.sent[0]["observations"]}
    assert "indirect" in kinds


def test_indirect_suppressed_when_disabled(tmp_path):
    cfg = make_config(tmp_path, extra="[observations]\nindirect = false\n")
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]
    mon.handle_line(r, "024000  -1 173  1.30  500.0 CQ KE2CUR FN30")
    mon.handle_line(r, "024000  -1 173  1.30  600.0 CQ KF9UG  EN71")
    mon.handle_line(r, "024015  -7 147  1.34  327.6 KE2CUR KF9UG  -02")
    mon.flush()
    kinds = {o["type"] for o in sender.sent[0]["observations"]}
    assert "indirect" not in kinds


def test_flush_failure_requeues_then_recovers(tmp_path):
    cfg = make_config(tmp_path)
    sender = RecordingSender(ok=False)
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]
    mon.handle_line(r, "024015   0 174  1.31 2185.1 CQ  W3GO  FN20")
    assert mon.flush() is False
    assert len(mon.queue) == 1                     # observation re-queued

    sender.ok = True
    assert mon.flush() is True
    assert len(mon.queue) == 0
    assert len(sender.sent[0]["observations"]) == 1


class FakeDriver:
    def __init__(self):
        self.bounces = 0

    def bounce(self):
        self.bounces += 1


def test_empty_batch_suppressed_by_default(tmp_path):
    cfg = make_config(tmp_path)            # send_empty_batches defaults False
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]
    mon.handle_line(r, "02:37:00 decodes: 17")    # non-decode, no observations
    assert mon.flush() is True
    assert sender.sent == []                # nothing transmitted on an empty window


def test_watchdog_restarts_after_silent(tmp_path):
    # pin send_interval to 60s, so 3m of silence = 3 flush windows
    cfg = make_config(tmp_path, extra="send_interval = 60\n",
                      receiver_extra="restart_after_silent = 3m\n")
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock())
    drv = FakeDriver()
    mon._driver_by_name[cfg.receivers[0].name] = drv
    mon._check_watchdog()                   # 60s
    mon._check_watchdog()                   # 120s
    assert drv.bounces == 0                 # not yet at 180s
    mon._check_watchdog()                   # 180s >= 3m -> restart
    assert drv.bounces == 1


def test_watchdog_resets_on_decode(tmp_path):
    cfg = make_config(tmp_path, extra="send_interval = 60\n",
                      receiver_extra="restart_after_silent = 2m\n")
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock())
    r = cfg.receivers[0]
    drv = FakeDriver()
    mon._driver_by_name[r.name] = drv
    mon._check_watchdog()                                       # silent 60s
    mon.handle_line(r, "024015   0 174  1.31 2185.1 CQ  W3GO  FN20")  # a decode
    mon._check_watchdog()                                       # decode -> reset to 0
    mon._check_watchdog()                                       # silent 60s
    assert drv.bounces == 0
    mon._check_watchdog()                                       # silent 120s >= 2m -> restart
    assert drv.bounces == 1


def test_watchdog_disabled_by_default(tmp_path):
    cfg = make_config(tmp_path)            # no restart_after_silent -> disabled
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock())
    drv = FakeDriver()
    mon._driver_by_name[cfg.receivers[0].name] = drv
    for _ in range(10):
        mon._check_watchdog()
    assert drv.bounces == 0


def test_low_snr_ft8_decode_dropped_from_dataset(tmp_path):
    cfg = make_config(tmp_path)            # default min_decode_snr = -25
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]
    mon.handle_line(r, "024015 -30 174  1.31 2185.1 CQ  W3GO  FN20")   # below -25: dropped
    mon.handle_line(r, "024015 -10 175  1.32 2200.0 CQ  K1ABC FN42")   # above -25: kept
    mon.flush()
    batch = sender.sent[0]
    grids = {o["tx_grid"] for o in batch["observations"]}
    assert grids == {"FN42"}                       # only the trustworthy decode
    assert batch["stats"]["decodes_seen"] == 2     # both still counted as live decodes


def test_min_decode_snr_configurable_per_receiver(tmp_path):
    cfg = make_config(tmp_path, receiver_extra="min_decode_snr = -15\n")
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]
    mon.handle_line(r, "024015 -20 174  1.31 2185.1 CQ  W3GO  FN20")   # -20 < -15: dropped
    mon.flush()
    assert sender.sent == []                        # nothing kept -> empty window suppressed


def test_sdrfanout_driver_creates_fifos_and_plan(tmp_path):
    import os
    import stat
    runtime = tmp_path / "run"
    ini = (
        "[monitor]\ngrid = FN19\n[collector]\naddress = abc\n"
        f"[storage]\ndir = {tmp_path}\n"
        f"[sdr]\ndriver = hackrf\nworking_dir = {runtime}\n"
        "[receiver:40m-ft8]\nfreq = 7074000\nsdr = yes\n"
        "[receiver:40m-wspr]\nmode = wspr\nfreq = 7038600\nsdr = yes\n"
    )
    p = tmp_path / "m.ini"
    p.write_text(ini)
    cfg = load(str(p))
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock())

    drv = mon._sdrfanout_driver()
    assert drv is not None and drv.capture_stderr
    assert drv.argv[:3] == [cfg.sdr.path, "-driver", "hackrf"]
    assert "-ch" in drv.argv                       # channels present
    # both FIFOs were created as real FIFOs under the sdrfanout/ subdir
    for name in ("40m-ft8", "40m-wspr"):
        f = runtime / "sdrfanout" / f"{name}.fifo"
        assert f.exists() and stat.S_ISFIFO(os.stat(f).st_mode)


def test_sdrfanout_driver_none_without_streams(tmp_path):
    cfg = make_config(tmp_path)                    # a card receiver, no [sdr]
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock())
    assert mon._sdrfanout_driver() is None


def test_verbose_echoes_decodes(tmp_path, capsys):
    cfg = make_config(tmp_path)
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock(), verbose=True)
    r = cfg.receivers[0]
    mon.handle_line(r, "024015   0 174  1.31 2185.1 CQ  W3GO  FN20")
    mon.handle_line(r, "02:37:00 decodes: 17")   # non-decode line, not echoed
    out = capsys.readouterr().out
    assert "CQ  W3GO  FN20" in out
    assert "decodes: 17" not in out


def test_verbose_hides_non_observations_by_default(tmp_path, capsys):
    # default (debug off): -v mirrors the collector, only kept observations show
    cfg = make_config(tmp_path)
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock(), verbose=True)
    r = cfg.receivers[0]
    mon.handle_line(r, "184545 -28 126  0.39 1335.5 i3=5 n3=6")        # unrendered type
    mon.handle_line(r, "184630 -28 130  2.18 1638.0 K1ABC W2DEF EM13") # SNR -28 < -25
    mon.handle_line(r, "024015 -10 174  1.31 2185.1 CQ  W3GO  FN20")   # kept observation
    out = capsys.readouterr().out
    assert "i3=5 n3=6" not in out
    assert "K1ABC W2DEF" not in out
    assert "CQ  W3GO  FN20" in out


def test_verbose_prefixes_mode_column(tmp_path, capsys):
    cfg = make_config(tmp_path, extra=(
        "[receiver:40m-wspr]\nmode = wspr\nband = 40m\nfreq = 7038600\ncard = 2\n"))
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock(), verbose=True)
    ft8 = next(r for r in cfg.receivers if r.mode == "ft8")
    wspr = next(r for r in cfg.receivers if r.mode == "wspr")
    mon.handle_line(ft8, "024015 -10 174  1.31 2185.1 CQ  W3GO  FN20")
    mon.handle_line(wspr, "091800  -9  1.1  14.097046  0  ND6P FN20 30")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert any(ln.startswith("FT8 ") and "W3GO" in ln for ln in lines)
    assert any(ln.startswith("WSPR ") and "ND6P" in ln for ln in lines)


def test_verbose_debug_shows_raw_firehose(tmp_path, capsys):
    cfg = make_config(tmp_path)
    cfg.monitor.debug = True
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock(), verbose=True)
    r = cfg.receivers[0]
    mon.handle_line(r, "184545 -28 126  0.39 1335.5 i3=5 n3=6")        # unrendered type
    mon.handle_line(r, "184630 -28 130  2.18 1638.0 K1ABC W2DEF EM13") # below the SNR cut
    out = capsys.readouterr().out
    assert "i3=5 n3=6" in out and "K1ABC W2DEF" in out                 # firehose


def test_blacklist_drops_decode_but_still_counts_it(tmp_path):
    cfg = make_config(tmp_path, extra="[blacklist]\ngrids = FN20\n")
    sender = RecordingSender()
    mon = Monitor(cfg, sender, clock=fixed_clock())
    r = cfg.receivers[0]
    mon.handle_line(r, "024015   0 174  1.31 2185.1 CQ  W3GO  FN20")    # blacklisted grid
    mon.handle_line(r, "024015   0 174  1.31 1000.0 CQ KE2CUR FN31")    # allowed
    assert mon.flush() is True
    obs = sender.sent[0]["observations"]
    assert {o["tx_grid"] for o in obs} == {"FN31"}                      # FN20 dropped
    assert sender.sent[0]["stats"]["decodes_seen"] == 2                 # both still counted
