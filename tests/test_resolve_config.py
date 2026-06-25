"""Tests for the CLI config-path resolver (default search order)."""

import pytest

from watchersattherim.common.config import ConfigError, resolve_config


def test_explicit_path_used_verbatim():
    # -c wins even if it doesn't exist (caller's load reports the missing file)
    assert resolve_config("/no/such.ini", ["a.ini", "b.ini"]) == "/no/such.ini"


def test_first_existing_candidate(tmp_path):
    a = tmp_path / "a.ini"
    b = tmp_path / "b.ini"
    b.write_text("")                       # only the second exists
    assert resolve_config(None, [str(a), str(b)]) == str(b)


def test_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "monitor.ini").write_text("")
    assert resolve_config(None, ["~/monitor.ini"]) == str(tmp_path / "monitor.ini")


def test_none_found_raises_listing_candidates():
    with pytest.raises(ConfigError, match="no config file found"):
        resolve_config(None, ["/nope1.ini", "/nope2.ini"])
    try:
        resolve_config(None, ["/nope1.ini"])
    except ConfigError as e:
        assert "/nope1.ini" in str(e) and "-c PATH" in str(e)
