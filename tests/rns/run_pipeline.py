#!/usr/bin/env python3
"""Local end-to-end pipeline test over real Reticulum/LXMF.

Starts the real collector and the real monitor (the repo versions, unmodified)
in isolated Reticulum instances linked over a loopback TCP interface, with a stub
ft8mon feeding the monitor decodes. Verifies that observations travel monitor ->
LXMF -> collector and land in the collector database, then that a real query
round-trips. Nothing here touches package code; the stub ft8mon and the configs
under tests/rns are external to the package.

Run from anywhere:  python tests/rns/run_pipeline.py
Requires RNS + LXMF importable. watchersattherim itself does not need to be
installed: the repo working tree is put on PYTHONPATH for the subprocesses, so
the test runs the source in this tree whether or not the package is installed.
The run prints which watchersattherim it imports and refuses to proceed if that
is not the repo tree.
"""

import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))      # repo root (parent of test/)
RUN = os.path.join(HERE, ".run")
PY = sys.executable

ENV = dict(os.environ)
ENV["PYTHONPATH"] = ROOT + os.pathsep + ENV.get("PYTHONPATH", "")
ENV["RNS_LOGLEVEL"] = "3"


def sh(args, **kw):
    return subprocess.run(args, cwd=HERE, env=ENV, text=True,
                          capture_output=True, **kw)


def module(name, *args):
    return [PY, "-m", name, *args]


def import_source():
    """Where the subprocesses will import watchersattherim from, under our env."""
    r = sh([PY, "-c", "import watchersattherim,sys; sys.stdout.write(watchersattherim.__file__)"])
    return r.stdout.strip()


def spawn(args, logpath):
    log = open(logpath, "w")
    # own session so we can reap the child ft8mon along with the parent
    return subprocess.Popen(args, cwd=HERE, env=ENV, stdout=log, stderr=log,
                            start_new_session=True), log


def wait_for(predicate, timeout, interval=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = predicate()
        if val:
            return val
        time.sleep(interval)
    return None


def obs_count(db_path):
    if not os.path.isfile(db_path):
        return 0
    try:
        con = sqlite3.connect(db_path)
        try:
            return con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def reset_run():
    shutil.rmtree(RUN, ignore_errors=True)
    for sub in ("reticulum/collector", "reticulum/monitor", "reticulum/query",
                "collector", "monitor/work", "query"):
        os.makedirs(os.path.join(RUN, sub), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "reticulum-collector.config"),
                    os.path.join(RUN, "reticulum/collector/config"))
    shutil.copyfile(os.path.join(HERE, "reticulum-monitor.config"),
                    os.path.join(RUN, "reticulum/monitor/config"))
    shutil.copyfile(os.path.join(HERE, "reticulum-query.config"),
                    os.path.join(RUN, "reticulum/query/config"))


def render_monitor_ini(collector_addr):
    tmpl = open(os.path.join(HERE, "monitor.ini.tmpl")).read()
    fake = os.path.join(HERE, "fake_ft8mon")
    rendered = tmpl.replace("__COLLECTOR_ADDR__", collector_addr).replace("__FT8MON__", fake)
    path = os.path.join(RUN, "monitor.ini")
    open(path, "w").write(rendered)
    return path


def terminate(proc, log):
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    log.close()


def tail(path, n=25):
    try:
        lines = open(path).read().splitlines()
        return "\n".join("    " + ln for ln in lines[-n:])
    except OSError:
        return "    (no log)"


def main():
    os.chmod(os.path.join(HERE, "fake_ft8mon"), 0o755)

    src = import_source()
    in_repo = src and os.path.realpath(src).startswith(os.path.realpath(ROOT) + os.sep)
    print(f"watchersattherim source: {src or '(import failed)'}")
    if not in_repo:
        print("refusing to run: the subprocesses would not import this repo's working tree")
        return 1

    reset_run()

    # Mint identities and read both LXMF addresses deterministically (-i).
    col = sh(module("watchersattherim.collector.cli", "-c", "collector.ini", "-i"))
    collector_addr = col.stdout.strip().splitlines()[-1] if col.stdout.strip() else ""
    if len(collector_addr) != 32:
        print("could not read collector address:\n", col.stdout, col.stderr)
        return 1
    monitor_ini = render_monitor_ini(collector_addr)
    mon = sh(module("watchersattherim.monitor.cli", "-c", ".run/monitor.ini", "-i"))
    monitor_addr = mon.stdout.strip().splitlines()[-1] if mon.stdout.strip() else ""
    print(f"collector {collector_addr}")
    print(f"monitor   {monitor_addr}")

    collector_db = os.path.join(RUN, "collector", "collector.db")
    results = []
    cproc = mproc = None
    clog = mlog = None
    try:
        cproc, clog = spawn(module("watchersattherim.collector.cli", "-c", "collector.ini"),
                            os.path.join(RUN, "collector.log"))
        ready = wait_for(
            lambda: "collector LXMF address" in open(os.path.join(RUN, "collector.log")).read(),
            timeout=20)
        results.append(("collector starts and registers its LXMF destination", bool(ready)))

        mproc, mlog = spawn(
            module("watchersattherim.monitor.cli", "-c", ".run/monitor.ini", "-v"),
            os.path.join(RUN, "monitor.log"))

        # The monitor must discover a path to the collector, then telemetry flows.
        count = wait_for(lambda: obs_count(collector_db) or None, timeout=150, interval=2.0)
        results.append(("observations reach the collector db over LXMF", bool(count)))
        print(f"observations ingested: {count or 0}")

        if count:
            ok = False
            q = None
            for _attempt in range(3):
                q = sh(module("watchersattherim.query.cli", collector_addr, "monitors",
                              "--config-dir", ".run/reticulum/query",
                              "--identity", ".run/query/identity", "--timeout", "45"))
                ok = '"ok": true' in q.stdout and monitor_addr in q.stdout
                if ok:
                    break
            results.append(("watr-query round-trips and lists the monitor", ok))
            print("query output:\n", (q.stdout or q.stderr) if q else "(none)")
    finally:
        if mproc:
            terminate(mproc, mlog)
        if cproc:
            terminate(cproc, clog)

    print()
    passed = sum(1 for _n, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{passed}/{len(results)} passed")
    if passed != len(results):
        print("\n--- collector.log (tail) ---")
        print(tail(os.path.join(RUN, "collector.log")))
        print("--- monitor.log (tail) ---")
        print(tail(os.path.join(RUN, "monitor.log")))
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
