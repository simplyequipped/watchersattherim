"""Tests for the Monitor orchestration (handle_line + flush), using fakes."""

from watchersattherim.monitor.config import load
from watchersattherim.monitor.monitor import Monitor


def make_config(tmp_path, extra=""):
    ini = f"""
[monitor]
grid = FN19
[receiver:20m]
freq = 14074000
card = 8
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


def test_watchdog_restarts_after_silent_cycles(tmp_path):
    cfg = make_config(tmp_path, extra="[ft8mon]\nrestart_after_silent_cycles = 3\n")
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock())
    drv = FakeDriver()
    mon._driver_by_band[cfg.receivers[0].band] = drv
    mon._check_watchdog()
    mon._check_watchdog()
    assert drv.bounces == 0                 # not yet at the limit
    mon._check_watchdog()
    assert drv.bounces == 1                 # third silent window -> restart


def test_watchdog_resets_on_decode(tmp_path):
    cfg = make_config(tmp_path, extra="[ft8mon]\nrestart_after_silent_cycles = 2\n")
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock())
    r = cfg.receivers[0]
    drv = FakeDriver()
    mon._driver_by_band[r.band] = drv
    mon._check_watchdog()                                       # silent 1
    mon.handle_line(r, "024015   0 174  1.31 2185.1 CQ  W3GO  FN20")  # a decode
    mon._check_watchdog()                                       # decode -> reset
    mon._check_watchdog()                                       # silent 1
    assert drv.bounces == 0
    mon._check_watchdog()                                       # silent 2 -> restart
    assert drv.bounces == 1


def test_watchdog_disabled_by_default(tmp_path):
    cfg = make_config(tmp_path)            # restart_after_silent_cycles defaults 0
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock())
    drv = FakeDriver()
    mon._driver_by_band[cfg.receivers[0].band] = drv
    for _ in range(10):
        mon._check_watchdog()
    assert drv.bounces == 0


def test_verbose_echoes_decodes(tmp_path, capsys):
    cfg = make_config(tmp_path)
    mon = Monitor(cfg, RecordingSender(), clock=fixed_clock(), verbose=True)
    r = cfg.receivers[0]
    mon.handle_line(r, "024015   0 174  1.31 2185.1 CQ  W3GO  FN20")
    mon.handle_line(r, "02:37:00 decodes: 17")   # non-decode line, not echoed
    out = capsys.readouterr().out
    assert "CQ  W3GO  FN20" in out
    assert "decodes: 17" not in out
