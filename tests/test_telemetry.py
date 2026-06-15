"""Tests for telemetry batching, encoding, and the pending-queue flush logic."""

from watchersattherim.monitor.observations import Observation
from watchersattherim.monitor.telemetry import (
    TelemetryBatcher, build_batch, decode, encode, monitor_meta,
)
from watchersattherim.monitor.transport import PendingQueue, flush_window


def _obs(kind="direct", snr=-7):
    return Observation(
        kind=kind,
        tx_grid="EN71", tx_lat=41.5, tx_lon=-88.0,
        rx_grid="FN19", rx_lat=49.5, rx_lon=-77.0,
        snr_db=snr, tx_call="KF9UG", rx_call="W1MON",
    )


def test_monitor_meta_fills_latlon_from_grid():
    m = monitor_meta("FN19", "watchers-0.1.0")
    assert m["grid"] == "FN19" and m["sw_version"] == "watchers-0.1.0"
    assert round(m["lat"], 1) == 49.5 and round(m["lon"], 1) == -77.0


def test_batcher_row_shape_and_counts():
    b = TelemetryBatcher()
    b.note_decode()
    b.note_decode()
    b.add(_obs(), ts=1717805412, freq_hz=14074500, band="20m")
    rows = b.take()
    assert len(rows) == 1 and len(b) == 0
    r = rows[0]
    assert r["ts"] == 1717805412 and r["freq"] == 14074500
    assert r["band"] == "20m" and r["mode"] == "FT8" and r["type"] == "direct"
    assert r["tx_grid"] == "EN71" and r["snr"] == -7
    assert "tx_call" not in r           # callsigns off by default
    assert b.decodes_seen == 2


def test_callsigns_included_when_enabled():
    b = TelemetryBatcher(include_callsigns=True)
    b.add(_obs(), ts=1, freq_hz=14074000, band="20m")
    r = b.take()[0]
    assert r["tx_call"] == "KF9UG" and r["rx_call"] == "W1MON"


def test_build_batch_matches_spec_shape():
    rows = [_obs_row()]
    batch = build_batch(monitor_meta("FN19", "sw"), 100, 160, rows,
                        decodes_seen=5, cache_size=42)
    assert batch["v"] == 1
    assert batch["window"] == {"start": 100, "end": 160}
    assert batch["stats"]["decodes_seen"] == 5
    assert batch["stats"]["obs_emitted"] == 1
    assert batch["stats"]["cache_size"] == 42
    assert batch["observations"] == rows


def test_encode_decode_round_trip():
    rows = [_obs_row()]
    batch = build_batch(monitor_meta("FN19", "sw"), 100, 160, rows)
    assert decode(encode(batch)) == batch


def _obs_row():
    b = TelemetryBatcher()
    b.add(_obs(), ts=1, freq_hz=14074000, band="20m")
    return b.take()[0]


# --- pending queue / flush ------------------------------------------------

class FakeSender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent: list[dict] = []

    def send(self, batch: dict) -> bool:
        if self.ok:
            self.sent.append(batch)
            return True
        return False


def test_pending_queue_drops_oldest_over_capacity():
    q = PendingQueue(max_observations=3)
    q.add_many([1, 2])
    q.add_many([3, 4, 5])     # now 5 items, cap 3 -> drop oldest 2 (1,2)
    assert len(q) == 3 and q.dropped == 2
    assert q.drain() == [3, 4, 5]


def test_flush_success_clears_and_sends():
    b = TelemetryBatcher()
    b.note_decode()
    b.add(_obs(), ts=1, freq_hz=14074000, band="20m")
    q = PendingQueue(max_observations=100)
    s = FakeSender(ok=True)

    ok = flush_window(batcher=b, queue=q, sender=s,
                      monitor=monitor_meta("FN19", "sw"),
                      window_start=0, window_end=60, cache_size=3)
    assert ok is True
    assert len(s.sent) == 1
    assert len(q) == 0 and len(b) == 0 and b.decodes_seen == 0
    # the sent batch is a well-formed dict (LXMF handles wire encoding)
    batch = s.sent[0]
    assert batch["stats"]["obs_emitted"] == 1
    assert batch["stats"]["cache_size"] == 3


def test_flush_skips_empty_batch_when_send_empty_false():
    b = TelemetryBatcher()
    b.note_decode()                 # decodes seen, but no observations produced
    q = PendingQueue(max_observations=100)
    s = FakeSender(ok=True)

    ok = flush_window(batcher=b, queue=q, sender=s,
                      monitor=monitor_meta("FN19", "sw"),
                      window_start=0, window_end=60, send_empty=False)
    assert ok is True               # window closed cleanly
    assert s.sent == []             # but nothing was transmitted
    assert b.decodes_seen == 0      # counter still reset


def test_flush_empty_still_sends_when_send_empty_true():
    b = TelemetryBatcher()
    q = PendingQueue(max_observations=100)
    s = FakeSender(ok=True)
    ok = flush_window(batcher=b, queue=q, sender=s,
                      monitor=monitor_meta("FN19", "sw"),
                      window_start=0, window_end=60, send_empty=True)
    assert ok is True and len(s.sent) == 1


def test_flush_sends_pending_backlog_even_when_send_empty_false():
    # A quiet window with nothing new but a pending backlog must still flush.
    b = TelemetryBatcher()
    q = PendingQueue(max_observations=100)
    q.add_many([_obs_row()])
    s = FakeSender(ok=True)
    ok = flush_window(batcher=b, queue=q, sender=s,
                      monitor=monitor_meta("FN19", "sw"),
                      window_start=0, window_end=60, send_empty=False)
    assert ok is True and len(s.sent) == 1
    assert s.sent[0]["stats"]["obs_emitted"] == 1


def test_flush_failure_requeues_observations():
    b = TelemetryBatcher()
    b.add(_obs(), ts=1, freq_hz=14074000, band="20m")
    q = PendingQueue(max_observations=100)
    s = FakeSender(ok=False)

    ok = flush_window(batcher=b, queue=q, sender=s,
                      monitor=monitor_meta("FN19", "sw"),
                      window_start=0, window_end=60)
    assert ok is False
    assert len(q) == 1          # observation returned to pending
    assert len(b) == 0          # batcher drained

    # next window the sender recovers: pending drains and is sent
    s.ok = True
    b.add(_obs(kind="indirect"), ts=2, freq_hz=14074000, band="20m")
    ok = flush_window(batcher=b, queue=q, sender=s,
                      monitor=monitor_meta("FN19", "sw"),
                      window_start=60, window_end=120)
    assert ok is True
    assert len(q) == 0
    batch = s.sent[0]
    assert batch["stats"]["obs_emitted"] == 2   # requeued + new
