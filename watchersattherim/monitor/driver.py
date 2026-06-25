"""Subprocess driver for a long-running child (ft8mon, wsprmon, or sdrfanout).

Decoupled from any message format: spawn a configured command, read one of its
streams line-by-line into a callback, and restart it on exit/crash. It can be
driven against a real binary or, in tests, a replay command (e.g. ``cat sample.txt``).

Two modes:
- default (decoders): read **stdout** (the decodes) into ``on_line``. **stderr** is
  discarded so untested, device-dependent ALSA/JACK noise can never reach the parser.
- ``capture_stderr=True`` (sdrfanout): the producer has no decode stdout. Its
  meaningful output (warnings, drops, the channel announce) is on **stderr**, so read
  stderr into the **logger** and discard stdout.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable, Optional, Sequence

LineHandler = Callable[[str], None]
Logger = Callable[[str], None]


class ReceiverDriver:
    def __init__(
        self,
        argv: Sequence[str],
        on_line: Optional[LineHandler] = None,
        *,
        capture_stderr: bool = False,
        log_command: bool = False,
        label: str = "receiver",
        restart_delay: float = 2.0,
        max_restarts: Optional[int] = None,
        sleep: Callable[[float], None] = time.sleep,
        popen: Optional[Callable[[Sequence[str]], "subprocess.Popen"]] = None,
        logger: Optional[Logger] = None,
    ):
        self.argv = list(argv)
        self.on_line = on_line or (lambda _line: None)
        self.capture_stderr = capture_stderr
        self.log_command = log_command
        self._label = label
        self.restart_delay = restart_delay
        self.max_restarts = max_restarts          # None = restart forever
        self._sleep = sleep
        self._popen = popen or self._default_popen
        self._log = logger or (lambda _msg: None)
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self.restarts = 0

    def _default_popen(self, argv: Sequence[str]) -> "subprocess.Popen":
        # capture_stderr: the producer's signal is on stderr, so discard its (unused)
        # stdout. Default: read decodes off stdout, discard device-noise stderr.
        read, drop = ((subprocess.PIPE, subprocess.DEVNULL) if not self.capture_stderr
                      else (subprocess.DEVNULL, subprocess.PIPE))
        return subprocess.Popen(
            argv,
            stdout=read,
            stderr=drop,
            text=True,
            bufsize=1,
            # Defensive: output is ASCII in practice, but a stray byte must never
            # break the read loop (which would wedge the reader in wait()).
            errors="replace",
        )

    def run(self) -> None:
        """Run the spawn/read/restart loop until ``stop()`` or max_restarts."""
        while not self._stop.is_set():
            proc = self._popen(self.argv)
            self._proc = proc
            if self.log_command:
                self._log(f"started {self._label}: {' '.join(self.argv)}")
            else:
                self._log(f"started {self._label}")
            stream = proc.stderr if self.capture_stderr else proc.stdout
            try:
                for line in stream:  # type: ignore[union-attr]
                    if self._stop.is_set():
                        break
                    text = line.rstrip("\n")
                    try:
                        if self.capture_stderr:
                            # Pass producer diagnostics through as-is. The child names
                            # itself (e.g. "sdrfanout: ...") and "started <label>" above
                            # tags the source, so re-prefixing here just duplicates it.
                            self._log(text)
                        else:
                            self.on_line(text)
                    except Exception as e:  # noqa: BLE001
                        # One malformed line must never kill the reader and orphan
                        # a still-running child. Log it and keep consuming output.
                        self._log(f"line handler error (skipped): {e}")
            finally:
                self._reap(proc)

            if self._stop.is_set():
                break

            self.restarts += 1
            if self.max_restarts is not None and self.restarts > self.max_restarts:
                self._log(f"stopped {self._label}: giving up (reached max restarts)")
                break
            self._log(f"stopped {self._label}: restarting...")
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
