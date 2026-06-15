"""Subprocess driver for ft8mon.

Generic and decoupled from FT8: spawn a configured command, read its stdout
line-by-line into a callback, and restart it on exit/crash. Knows nothing about
message formats, so it can be driven against a real ft8mon binary or, in tests,
a replay command (e.g. ``cat sample.txt``).

ft8mon emits decodes on stdout (verified against ft8mon.cc) and noisy,
device-dependent ALSA/JACK diagnostics on stderr. We discard stderr so that
uncontrolled output we have not tested per device can never reach the line
parser; only stdout is read.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable, Optional, Sequence

LineHandler = Callable[[str], None]
Logger = Callable[[str], None]


class Ft8monDriver:
    def __init__(
        self,
        argv: Sequence[str],
        on_line: LineHandler,
        *,
        restart_delay: float = 2.0,
        max_restarts: Optional[int] = None,
        sleep: Callable[[float], None] = time.sleep,
        popen: Optional[Callable[[Sequence[str]], "subprocess.Popen"]] = None,
        logger: Optional[Logger] = None,
    ):
        self.argv = list(argv)
        self.on_line = on_line
        self.restart_delay = restart_delay
        self.max_restarts = max_restarts          # None = restart forever
        self._sleep = sleep
        self._popen = popen or self._default_popen
        self._log = logger or (lambda _msg: None)
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self.restarts = 0

    @staticmethod
    def _default_popen(argv: Sequence[str]) -> "subprocess.Popen":
        return subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            # Discard ft8mon's device-dependent ALSA/JACK noise; only decodes (stdout)
            # should reach the parser. Run ft8mon directly to inspect its diagnostics.
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            # Defensive: stdout is ASCII in practice, but a stray byte must never
            # break the read loop (which would wedge the reader in wait()).
            errors="replace",
        )

    def run(self) -> None:
        """Run the spawn/read/restart loop until ``stop()`` or max_restarts."""
        while not self._stop.is_set():
            proc = self._popen(self.argv)
            self._proc = proc
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    if self._stop.is_set():
                        break
                    try:
                        self.on_line(line.rstrip("\n"))
                    except Exception as e:  # noqa: BLE001
                        # One malformed line must never kill the reader and orphan
                        # a still-running ft8mon; log it and keep consuming output.
                        self._log(f"line handler error (skipped): {e}")
            finally:
                rc = self._reap(proc)

            if self._stop.is_set():
                break

            self.restarts += 1
            if self.max_restarts is not None and self.restarts > self.max_restarts:
                self._log(f"ft8mon exited rc={rc}; max_restarts reached, giving up")
                break
            self._log(f"ft8mon exited rc={rc}; restarting in {self.restart_delay}s")
            self._sleep(self.restart_delay)

    @staticmethod
    def _reap(proc: "subprocess.Popen") -> Optional[int]:
        try:
            return proc.wait()
        except Exception:
            return None

    def stop(self) -> None:
        """Signal the loop to exit and terminate the running child, if any."""
        self._stop.set()
        proc = self._proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass

    def bounce(self) -> None:
        """Terminate the current child without stopping the loop, so it restarts.

        Used by the monitor's no-decode watchdog to recover a ft8mon that is alive
        but has gone silent (producing no decodes).
        """
        proc = self._proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
