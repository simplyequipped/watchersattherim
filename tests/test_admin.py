"""Tests for the LXMF admin text-command handler."""

from watchersattherim.collector import storage
from watchersattherim.collector.admin import handle_admin_command
from watchersattherim.collector.stats import Stats

NOW = 1_000_000
H = "cc" * 16


def db():
    return storage.connect(":memory:")


def test_help_on_empty():
    conn = db()
    assert "commands" in handle_admin_command(conn, Stats(conn), "", now=NOW).lower()


def test_allow_and_deny_monitor():
    conn = db()
    s = Stats(conn)
    out = handle_admin_command(conn, s, f"allow {H}", now=NOW)
    assert "allowed" in out and storage.is_allowed(conn, bytes.fromhex(H)) is True
    out = handle_admin_command(conn, s, f"deny {H}", now=NOW)
    assert "denied" in out and storage.is_allowed(conn, bytes.fromhex(H)) is False


def test_block_and_unblock_query():
    conn = db()
    s = Stats(conn)
    handle_admin_command(conn, s, f"block {H}", now=NOW)
    assert storage.is_query_blocked(conn, bytes.fromhex(H)) is True
    handle_admin_command(conn, s, f"unblock {H}", now=NOW)
    assert storage.is_query_blocked(conn, bytes.fromhex(H)) is False


def test_status():
    conn = db()
    s = Stats(conn)
    s.record_query("aa")
    s.refresh(now=NOW)
    out = handle_admin_command(conn, s, "status", now=NOW)
    assert "observations" in out and "queries" in out


def test_monitors_empty():
    conn = db()
    assert "no monitors" in handle_admin_command(conn, Stats(conn), "monitors", now=NOW)


def test_invalid_address():
    conn = db()
    assert "invalid" in handle_admin_command(conn, Stats(conn), "allow zzzz", now=NOW)


def test_unknown_command():
    conn = db()
    assert "unknown" in handle_admin_command(conn, Stats(conn), "frobnicate", now=NOW).lower()
