"""Tests for the watr-propagation CLI output helpers (csv, chart, timezone)."""

import pytest

from watchersattherim.propagation import cli


def test_to_csv_from_items():
    result = {"unit": "hour", "items": [
        {"hour": 0, "observations": 10, "quality": 0.5, "median_snr_db": -7}]}
    out = cli._to_csv(result, None).splitlines()
    assert out[0] == "hour,observations,quality,median_snr_db"
    assert out[1] == "0,10,0.5,-7"


def test_to_csv_flattens_path_bands():
    result = {"unit": "hour", "bands": {
        "40m": {"items": [{"hour": 1, "openness": 0.5, "quality": 0.6}]}}}
    out = cli._to_csv(result, None).splitlines()
    assert out[0] == "band,hour,openness,quality"
    assert out[1] == "40m,1,0.5,0.6"


def test_to_csv_flattens_nested_evidence():
    result = {"bands": [{"band": "40m", "quality": 0.7,
                         "evidence": {"observations": 5, "reciprocal": 4}}]}
    header = cli._to_csv(result, None).splitlines()[0]
    assert "evidence.observations" in header and "evidence.reciprocal" in header


def test_chart_defaults_to_quality_and_lists_metrics():
    result = {"unit": "hour", "band": "40m", "items": [
        {"hour": 0, "observations": 10, "quality": 0.5, "median_snr_db": -7},
        {"hour": 1, "observations": 20, "quality": 0.7, "median_snr_db": -3}]}
    out = cli._chart(result, None, "trend/band/hour")
    assert "metric: quality" in out
    assert "available metrics: observations, quality, median_snr_db" in out
    assert "#" in out and "40m" in out


def test_chart_selected_metric():
    result = {"unit": "hour", "band": "40m",
              "items": [{"hour": 0, "observations": 10, "quality": 0.5}]}
    assert "metric: observations" in cli._chart(result, "observations", "trend/band/hour")


def test_chart_rejects_non_trend():
    with pytest.raises(ValueError):
        cli._chart({"cells": []}, None, "map")


def test_resolve_timezone():
    assert cli._resolve_timezone("utc") == "UTC"
    assert cli._resolve_timezone(None) is None
    assert cli._resolve_timezone("America/New_York") == "America/New_York"
