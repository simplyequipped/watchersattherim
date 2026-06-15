"""Tests for the ft8mon subprocess driver.

Uses a fake Popen for lifecycle/restart logic and a real ``cat`` replay of the
captured sample for an end-to-end read-loop test (no ft8mon binary needed).
"""

from pathlib import Path

from watchersattherim.monitor.cache import CallsignCache
from watchersattherim.monitor.driver import Ft8monDriver
from watchersattherim.monitor.pipeline import handle_line

SAMPLE = Path(__file__).resolve().parent.parent / "docs/samples/ft8mon_output.txt"


class FakePopen:
    """Minimal Popen stand-in: stdout yields the given lines, then 'exits'."""

    def __init__(self, lines, returncode=0):
        self.stdout = iter(line + "\n" for line in lines)
        self.returncode = returncode
        self.terminated = False
        self._done = False

    def wait(self):
        self._done = True
        return self.returncode

    def poll(self):
        # None while "running", returncode once reaped - like real Popen.
        return self.returncode if self._done else None

    def terminate(self):
        self.terminated = True


# --- lifecycle / restart --------------------------------------------------

def test_reads_all_lines_then_stops():
    seen = []
    drv = Ft8monDriver(["fake"], on_line=seen.append, max_restarts=0,
                       popen=lambda argv: FakePopen(["a", "b", "c"]))
    drv.run()
    assert seen == ["a", "b", "c"]
    assert drv.restarts == 1  # one exit observed, but max_restarts=0 stops it


def test_restarts_on_exit():
    procs = [FakePopen(["1"]), FakePopen(["2"]), FakePopen(["3"])]
    spawned = []

    def popen(argv):
        p = procs[len(spawned)]
        spawned.append(p)
        return p

    seen = []
    delays = []
    drv = Ft8monDriver(
        ["fake"], on_line=seen.append,
        max_restarts=2, restart_delay=5.0,
        popen=popen, sleep=delays.append,
    )
    drv.run()
    assert seen == ["1", "2", "3"]   # ran 3 times (initial + 2 restarts)
    assert len(spawned) == 3
    assert delays == [5.0, 5.0]      # slept before each restart


def test_bad_line_does_not_kill_reader():
    # A handler that raises on one line must not stop the reader from consuming
    # the rest (the bug that orphaned a live ft8mon and produced empty batches).
    seen = []

    def on_line(line):
        if line == "boom":
            raise ValueError("bad line")
        seen.append(line)

    drv = Ft8monDriver(["fake"], on_line=on_line, max_restarts=0,
                       popen=lambda argv: FakePopen(["a", "boom", "b", "c"]))
    drv.run()
    assert seen == ["a", "b", "c"]


def test_bounce_terminates_current_child_without_stopping():
    drv = Ft8monDriver(["fake"], on_line=lambda _l: None)
    proc = FakePopen([])
    drv._proc = proc
    drv.bounce()
    assert proc.terminated is True       # current child killed
    assert not drv._stop.is_set()        # but the run loop is NOT stopped


def test_stop_terminates_child():
    proc = FakePopen(["x", "y", "z"])

    def on_line(line):
        if line == "x":
            drv.stop()  # stop mid-stream

    drv = Ft8monDriver(["fake"], on_line=on_line, popen=lambda argv: proc)
    drv.run()
    assert proc.terminated is True


# --- end-to-end replay ----------------------------------------------------

def test_replay_sample_through_pipeline_via_cat():
    cache = CallsignCache()
    observations = []

    def on_line(line):
        observations.extend(handle_line(line, cache, monitor_grid="FN19"))

    drv = Ft8monDriver(["cat", str(SAMPLE)], on_line=on_line, max_restarts=0)
    drv.run()

    # A busy band capture should yield plenty of direct observations and learn
    # a healthy callsign cache.
    assert len(observations) > 100
    assert any(o.kind == "direct" for o in observations)
    assert len(cache) > 50
