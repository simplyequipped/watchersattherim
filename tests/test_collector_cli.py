"""Tests for the collector CLI admin commands (allowlist management)."""

import time

from watchersattherim.collector import storage
from watchersattherim.collector.cli import _prune_due, main


def _st(year, mon, day, hour):
    return time.struct_time((year, mon, day, hour, 0, 0, 0, 0, -1))


def test_prune_due_schedule():
    # past the hour today, not yet run -> due
    assert _prune_due(_st(2026, 6, 12, 4), None, 3) is True
    # before the hour -> not due
    assert _prune_due(_st(2026, 6, 12, 2), None, 3) is False
    # already ran today -> not due again
    assert _prune_due(_st(2026, 6, 12, 4), (2026, 6, 12), 3) is False
    # new day, past the hour -> due
    assert _prune_due(_st(2026, 6, 13, 5), (2026, 6, 12), 3) is True

MON = "aa" * 16


def write_config(tmp_path):
    p = tmp_path / "collector.ini"
    p.write_text(f"[storage]\ndir = {tmp_path}\n")
    return str(p)


def db(tmp_path):
    return storage.connect(str(tmp_path / "collector.db"))


def test_allow_then_deny(tmp_path):
    cfg = write_config(tmp_path)

    assert main(["-c", cfg, "--allow", MON]) == 0
    assert storage.is_allowed(db(tmp_path), bytes.fromhex(MON)) is True

    assert main(["-c", cfg, "--deny", MON]) == 0
    assert storage.is_allowed(db(tmp_path), bytes.fromhex(MON)) is False


def test_allow_invalid_hash(tmp_path):
    cfg = write_config(tmp_path)
    assert main(["-c", cfg, "--allow", "nothex"]) == 2


def test_list_monitors(tmp_path, capsys):
    cfg = write_config(tmp_path)
    main(["-c", cfg, "--allow", MON])
    capsys.readouterr()  # clear

    assert main(["-c", cfg, "--list-monitors"]) == 0
    out = capsys.readouterr().out
    assert MON in out and "allowed" in out


def test_list_monitors_empty(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert main(["-c", cfg, "--list-monitors"]) == 0
    assert "no monitors known" in capsys.readouterr().out


def test_block_and_unblock_query(tmp_path):
    cfg = write_config(tmp_path)
    assert main(["-c", cfg, "--block", MON]) == 0
    assert storage.is_query_blocked(db(tmp_path), bytes.fromhex(MON)) is True
    assert main(["-c", cfg, "--unblock", MON]) == 0
    assert storage.is_query_blocked(db(tmp_path), bytes.fromhex(MON)) is False


def test_finds_collector_ini_in_cwd(tmp_path, monkeypatch):
    # no -c: ./collector.ini is found and used
    monkeypatch.chdir(tmp_path)
    (tmp_path / "collector.ini").write_text(f"[storage]\ndir = {tmp_path}\n")
    assert main(["--list-monitors"]) == 0


def test_no_config_anywhere_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                  # no ./collector.ini
    monkeypatch.setenv("HOME", str(tmp_path))    # no ~/.watchersattherim/collector/...
    assert main(["--list-monitors"]) == 2
