"""Tests for the query client's param/envelope helpers (live path untested)."""

import pytest

from watchersattherim.query.cli import build_query, parse_params


def test_parse_params():
    assert parse_params(["tx_grid=FN42", "rx_grid=FN19", "hours=4"]) == {
        "tx_grid": "FN42", "rx_grid": "FN19", "hours": "4",
    }


def test_parse_params_empty():
    assert parse_params([]) == {}


def test_parse_params_value_with_equals():
    assert parse_params(["a=b=c"]) == {"a": "b=c"}


def test_parse_params_rejects_bare_token():
    with pytest.raises(ValueError):
        parse_params(["notakeyvalue"])


def test_build_query_shape():
    env = build_query("path_query", {"tx_grid": "FN42"}, "abc123")
    assert env == {
        "v": 1, "cmd": "path_query",
        "params": {"tx_grid": "FN42"}, "request_id": "abc123",
    }
