"""Tests for the propagation layer: geo, channel, trend, map, coverage."""

import itertools
from datetime import datetime, timezone

import pytest

from watchersattherim.collector import storage
from watchersattherim.collector.commands import CommandError, INVALID_PARAMS, dispatch
from watchersattherim.collector.stats import Stats
from watchersattherim.propagation import channel, field, geo, trend
from watchersattherim.propagation.config import PropagationConfig

MON = b"\x01" * 16
AT = 1_700_000_000          # fixed epoch; subtracting whole days keeps the UTC hour
_freq = itertools.count(7074000)   # unique freq so the dedup index never collapses rows


def make_conn(tmp_path, name="c.db"):
    conn = storage.connect(str(tmp_path / name))
    storage.upsert_monitor(conn, MON, grid="FN19", now=AT)
    return conn


def add(conn, *, tx_grid, rx_grid, snr, ts, band="40m", type="direct",
        mode="FT8", power=None):
    row = {
        "ts": ts, "mode": mode, "band": band, "freq": next(_freq),
        "tx_lat": 0.0, "tx_lon": 0.0, "tx_grid": tx_grid,
        "rx_lat": 0.0, "rx_lon": 0.0, "rx_grid": rx_grid,
        "snr": snr, "type": type,
    }
    if power is not None:
        row["power_dbm"] = power
    storage.insert_observations(conn, MON, [row])


# --- geo --------------------------------------------------------------------

def test_geo_roundtrip_and_distance():
    assert geo.resolution_chars("medium") == 4
    assert geo.latlon_to_grid(*geo.grid_center("FN42"), 4) == "FN42"
    # FN19 to EM12 is a real, nonzero distance
    a, b = geo.grid_center("FN19"), geo.grid_center("EM12")
    assert geo.haversine_km(a[0], a[1], b[0], b[1]) > 100
    assert geo.parse_point("FN19") == "FN19"
    assert geo.parse_point("40.5,-74.0")[:2] == "FN"


# --- channel ----------------------------------------------------------------

def test_channel_forward_match(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-5 - i, ts=AT - 60 - i)
    r = channel.estimate(conn, origin="FN19", dest="EM12", at=AT)
    assert r["ranked"] == ["40m"] and r["rank"] == "ft8"
    assert r["origin"]["grid"] == "FN19" and r["dest"]["grid"] == "EM12"
    ft8 = r["bands"]["40m"]["ft8"]
    assert ft8["observations"] == 4
    assert ft8["reciprocal"] == 0
    assert ft8["match_precision"] == 4
    assert "median_snr_db" in ft8 and 0.0 <= ft8["quality"] <= 1.0
    assert r["bands"]["40m"]["wspr"] is None       # no WSPR data for this path


def test_channel_distance_and_units(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-5, ts=AT - 60 - i)
    km = channel.estimate(conn, origin="FN19", dest="EM12", at=AT)
    mi = channel.estimate(conn, origin="FN19", dest="EM12", at=AT, units="mi")
    assert km["units"] == "km" and km["distance"] > 0
    assert mi["units"] == "mi" and mi["distance"] < km["distance"]   # miles < km
    assert 0.0 <= km["bearing"] <= 360.0


def test_channel_reciprocal_counted(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-3, ts=AT - 30 - i)
    ft8 = channel.estimate(conn, origin="FN19", dest="EM12", at=AT)["bands"]["40m"]["ft8"]
    assert ft8["reciprocal"] == 4


def test_channel_window_excludes_old(tmp_path):
    conn = make_conn(tmp_path)
    add(conn, tx_grid="FN19", rx_grid="EM12", snr=-5, ts=AT - 60)
    add(conn, tx_grid="FN19", rx_grid="EM12", snr=-5, ts=AT - 10_000)
    r = channel.estimate(conn, origin="FN19", dest="EM12", window_sec=1800, at=AT)
    assert r["bands"]["40m"]["ft8"]["observations"] == 1


def test_channel_no_widen(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-5, ts=AT - 60 - i)
    assert channel.estimate(conn, origin="FN19XX", dest="EM12AA", at=AT)["ranked"] == ["40m"]
    assert channel.estimate(conn, origin="FN19XX", dest="EM12AA", at=AT, widen=False)["ranked"] == []


def test_channel_wspr_ref_snr_and_fallback(tmp_path):
    conn = make_conn(tmp_path)
    # WSPR beacons at +30 dBm heard at -16; default ref 37 -> ref_snr = -16 + (37-30) = -9
    for i in range(4):
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-16, ts=AT - 60 - i,
            mode="WSPR", power=30)
    r = channel.estimate(conn, origin="FN19", dest="EM12", at=AT)
    block = r["bands"]["40m"]
    assert block["ft8"] is None                       # no FT8 data for this path
    assert block["wspr"]["median_power_dbm"] == 30 and block["wspr"]["median_snr_db"] == -16
    assert block["wspr"]["ref_snr_db"] == -9
    assert r["ref_power_dbm"] == 37
    assert r["rank"] == "wspr" and r["ranked"] == ["40m"]   # requested ft8, fell back


def test_channel_ref_power_arg_shifts_ref_snr(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-16, ts=AT - 60 - i,
            mode="WSPR", power=30)
    r = channel.estimate(conn, origin="FN19", dest="EM12", at=AT, ref_power_dbm=43)
    assert r["ref_power_dbm"] == 43
    assert r["bands"]["40m"]["wspr"]["ref_snr_db"] == -3   # -16 + (43-30)


def test_channel_both_modes_symmetric(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-5, ts=AT - 60 - i)            # FT8
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-16, ts=AT - 60 - i,
            mode="WSPR", power=30)                                                    # WSPR
    block = channel.estimate(conn, origin="FN19", dest="EM12", at=AT)["bands"]["40m"]
    assert "quality" in block["ft8"] and "ref_snr_db" in block["wspr"]


def test_channel_rank_wspr_orders_by_ref_snr(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-10, ts=AT - 60 - i, band="40m",
            mode="WSPR", power=30)    # ref_snr -3
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-20, ts=AT - 60 - i, band="20m",
            mode="WSPR", power=30)    # ref_snr -13
    r = channel.estimate(conn, origin="FN19", dest="EM12", at=AT, rank="wspr")
    assert r["rank"] == "wspr" and r["ranked"] == ["40m", "20m"]


def test_channel_anomaly_depressed(tmp_path):
    conn = make_conn(tmp_path)
    # normally open at this hour over the last few days, but silent right now
    for d in (1, 2, 3):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-6, ts=AT - d * 86400, band="20m")
    r = channel.anomaly(conn, origin="FN19", dest="EM12", at=AT, timezone="UTC")
    band = next(b for b in r["bands"] if b["band"] == "20m")
    assert band["baseline"]["openness"] > 0
    assert band["deviation"] < 0           # open historically, dead now


# --- trend ------------------------------------------------------------------

def test_trend_path_hour(tmp_path):
    conn = make_conn(tmp_path)
    for d in range(3):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-7, ts=AT - d * 86400, band="20m")
    r = trend.path(conn, origin="FN19", dest="EM12", unit="hour",
                   timezone="UTC", max_window_sec=30 * 86400, now=AT)
    assert "20m" in r["bands"]
    item = r["bands"]["20m"]["items"][0]
    assert "hour" in item and 0.0 <= item["openness"] <= 1.0
    assert item["observations"] == 3 and "median_snr_db" in item


def test_trend_path_anomaly_series(tmp_path):
    conn = make_conn(tmp_path)
    # decodes every day at the same UTC hour except none in the target span
    base_hour = (AT // 3600) * 3600
    for d in range(1, 6):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-6, ts=base_hour - d * 86400, band="20m")
    r = trend.path_anomaly(conn, origin="FN19", dest="EM12", bands=None,
                           start=base_hour - 6 * 86400, end=base_hour, timezone="UTC")
    items = r["bands"]["20m"]["items"]
    assert len(items) > 100                      # continuous hourly series over ~6 days
    assert all("time" in it and "deviation" in it for it in items)


def test_trend_band_hour(tmp_path):
    conn = make_conn(tmp_path)
    for d in range(3):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-8, ts=AT - d * 86400, band="40m")
        add(conn, tx_grid="EN61", rx_grid="FN19", snr=-9, ts=AT - d * 86400, band="40m")
    r = trend.band(conn, band="40m", unit="hour", timezone="UTC",
                   max_window_sec=30 * 86400, now=AT)
    item = r["items"][0]
    assert item["grids"] == 2 and "openness" not in item
    assert item["observations"] == 6 and "quality" in item
    assert r["units"] == "km" and item["distance"] > 0     # median reach this bucket


# --- map / coverage ---------------------------------------------------------

def test_map_field_cells(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(5):
        add(conn, tx_grid="FN20", rx_grid="FN19", snr=-5, ts=AT - 60 - i, band="40m")
    r = field.map_field(conn, origin="FN19", radius_km=3000, band="40m",
                        resolution="medium", at=AT)
    assert r["cells"], "expected at least one cell"
    cell = r["cells"][0]
    assert cell["grid"] == "FN20" and "quality" in cell and "median_snr_db" in cell


def test_coverage_best_band(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=2, ts=AT - 30 - i, band="20m")
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-18, ts=AT - 30 - i, band="40m")
    r = field.coverage(conn, origin="FN19", radius_km=4000, resolution="medium", at=AT)
    cell = next(c for c in r["cells"] if c["grid"] == "EM12")
    assert cell["ft8"]["band"] == "20m"            # stronger band wins as best
    assert "confidence" in cell["ft8"] and cell["ft8"]["observations"] == 4
    assert cell["wspr"] is None                    # no WSPR data


def test_coverage_wspr_per_mode(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-5, ts=AT - 30 - i, band="20m")   # FT8
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-16, ts=AT - 30 - i, band="40m",
            mode="WSPR", power=30)                                                       # WSPR
    r = field.coverage(conn, origin="FN19", radius_km=4000, resolution="medium", at=AT)
    cell = next(c for c in r["cells"] if c["grid"] == "EM12")
    assert cell["ft8"]["band"] == "20m" and "quality" in cell["ft8"]
    assert cell["wspr"]["band"] == "40m" and cell["wspr"]["ref_snr_db"] == -9
    assert r["ref_power_dbm"] == 37


# --- dispatch: resource caps -----------------------------------------------

def _dispatch(conn, cmd, params, *, now=AT, prop=None):
    return dispatch(conn, Stats(conn), cmd, params, now=now, propagation=prop or PropagationConfig())


def test_cap_radius(tmp_path):
    conn = make_conn(tmp_path)
    r = _dispatch(conn, "map", {"origin": "FN19", "band": "40m", "radius": "5000"},
                  prop=PropagationConfig(max_radius_km=1000))
    assert r["radius"] == 1000.0          # clamped from 5000


def test_cap_window(tmp_path):
    conn = make_conn(tmp_path)
    r = _dispatch(conn, "channel", {"origin": "FN19", "dest": "EM12", "window": "2h"},
                  prop=PropagationConfig(max_window_sec=600))
    assert r["window"] == "10m"           # 2h clamped to 600s


def test_cap_cells(tmp_path):
    conn = make_conn(tmp_path)
    for _ in range(3):
        add(conn, tx_grid="FN20", rx_grid="FN19", snr=-5, ts=AT - 60, band="40m")
        add(conn, tx_grid="FN30", rx_grid="FN19", snr=-5, ts=AT - 60, band="40m")
    r = _dispatch(conn, "map", {"origin": "FN19", "band": "40m", "radius": "5000"},
                  prop=PropagationConfig(max_cells=1))
    assert len(r["cells"]) == 1


# --- dispatch: param validation --------------------------------------------

def test_invalid_units(tmp_path):
    conn = make_conn(tmp_path)
    with pytest.raises(CommandError) as e:
        _dispatch(conn, "channel", {"origin": "FN19", "dest": "EM12", "units": "furlongs"})
    assert e.value.code == INVALID_PARAMS


def test_invalid_resolution(tmp_path):
    conn = make_conn(tmp_path)
    with pytest.raises(CommandError) as e:
        _dispatch(conn, "map", {"origin": "FN19", "resolution": "potato"})
    assert e.value.code == INVALID_PARAMS


def test_coverage_requires_exactly_one_endpoint(tmp_path):
    conn = make_conn(tmp_path)
    with pytest.raises(CommandError) as e:
        _dispatch(conn, "coverage", {"origin": "FN19", "dest": "EM12"})
    assert e.value.code == INVALID_PARAMS
    with pytest.raises(CommandError) as e2:
        _dispatch(conn, "coverage", {})
    assert e2.value.code == INVALID_PARAMS


def test_dispatch_channel_ref_power_and_rank(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="FN19", rx_grid="EM12", snr=-16, ts=AT - 60 - i,
            mode="WSPR", power=30)
    r = _dispatch(conn, "channel", {"origin": "FN19", "dest": "EM12",
                                    "ref_power_dbm": "43", "rank": "wspr"})
    assert r["ref_power_dbm"] == 43 and r["rank"] == "wspr"
    assert r["bands"]["40m"]["wspr"]["ref_snr_db"] == -3


@pytest.mark.parametrize("cmd,params", [
    ("channel", {"origin": "FN19", "dest": "EM12", "rank": "jt65"}),
    ("channel", {"origin": "FN19", "dest": "EM12", "ref_power_dbm": "loud"}),
    ("map", {"origin": "FN19", "mode": "jt65"}),
])
def test_dispatch_invalid_propagation_args(tmp_path, cmd, params):
    conn = make_conn(tmp_path)
    with pytest.raises(CommandError) as e:
        _dispatch(conn, cmd, params)
    assert e.value.code == INVALID_PARAMS


# --- coverage / map variants ------------------------------------------------

def test_coverage_dest_mode(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-4, ts=AT - 30 - i, band="20m")
    r = field.coverage(conn, dest="FN19", radius_km=4000, resolution="medium", at=AT)
    assert "dest" in r and r["dest"]["grid"] == "FN19"
    assert any(c["grid"] == "EM12" for c in r["cells"])


def test_coverage_single_band_forced(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(4):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=2, ts=AT - 30 - i, band="20m")
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-18, ts=AT - 30 - i, band="40m")
    r = field.coverage(conn, origin="FN19", band="40m", radius_km=4000, resolution="medium", at=AT)
    cell = next(c for c in r["cells"] if c["grid"] == "EM12")
    assert cell["ft8"]["band"] == "40m"          # forced to the requested band, not best (20m)


def test_map_radius_excludes_far(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(3):
        add(conn, tx_grid="FN20", rx_grid="FN19", snr=-5, ts=AT - 60 - i, band="40m")  # near
        add(conn, tx_grid="JO65", rx_grid="FN19", snr=-5, ts=AT - 60 - i, band="40m")  # EU, far
    r = field.map_field(conn, origin="FN19", radius_km=2000, band="40m", resolution="medium", at=AT)
    grids = {c["grid"] for c in r["cells"]}
    assert "FN20" in grids and "JO65" not in grids


def test_map_resolution_changes_cell_size(tmp_path):
    conn = make_conn(tmp_path)
    for i in range(3):
        add(conn, tx_grid="EM48", rx_grid="FN19", snr=-5, ts=AT - 60 - i, band="40m")
    coarse = field.map_field(conn, origin="FN19", radius_km=5000, band="40m", resolution="coarse", at=AT)
    medium = field.map_field(conn, origin="FN19", radius_km=5000, band="40m", resolution="medium", at=AT)
    assert coarse["cells"][0]["grid"] == "EM"       # field (2 char)
    assert medium["cells"][0]["grid"] == "EM48"     # square (4 char)


# --- trend variants ---------------------------------------------------------

def test_trend_band_region_excludes_far(tmp_path):
    conn = make_conn(tmp_path)
    for d in range(2):
        add(conn, tx_grid="FN18", rx_grid="FN19", snr=-5, ts=AT - d * 86400, band="40m")  # ~111km
        add(conn, tx_grid="JO65", rx_grid="FN19", snr=-5, ts=AT - d * 86400, band="40m")  # EU, far
    r = trend.band(conn, band="40m", unit="hour", origin="FN19", radius_km=1000,
                   timezone="UTC", max_window_sec=30 * 86400, now=AT)
    assert sum(it["observations"] for it in r["items"]) == 2   # only the near transmitter
    assert r["region"]["origin"]["grid"] == "FN19"


def test_trend_path_month_filter(tmp_path):
    conn = make_conn(tmp_path)
    jun = int(datetime(2023, 6, 15, 12, tzinfo=timezone.utc).timestamp())
    jul = int(datetime(2023, 7, 15, 12, tzinfo=timezone.utc).timestamp())
    add(conn, tx_grid="EM12", rx_grid="FN19", snr=-6, ts=jun, band="20m")
    add(conn, tx_grid="EM12", rx_grid="FN19", snr=-6, ts=jul, band="20m")
    r = trend.path(conn, origin="FN19", dest="EM12", unit="hour", filters={"month": 6},
                   timezone="UTC", max_window_sec=400 * 86400, now=jul + 60)
    items = r["bands"]["20m"]["items"]
    assert sum(it["observations"] for it in items) == 1        # June only


def test_trend_timezone_shifts_bucket(tmp_path):
    conn = make_conn(tmp_path)
    ts = int(datetime(2023, 6, 15, 12, 0, tzinfo=timezone.utc).timestamp())   # 12:00 UTC
    for _ in range(2):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-6, ts=ts, band="20m")
    utc = trend.path(conn, origin="FN19", dest="EM12", unit="hour", timezone="UTC",
                     max_window_sec=10 * 86400, now=ts + 60)
    est = trend.path(conn, origin="FN19", dest="EM12", unit="hour", timezone="Etc/GMT+5",
                     max_window_sec=10 * 86400, now=ts + 60)   # UTC-5
    assert utc["bands"]["20m"]["items"][0]["hour"] == 12
    assert est["bands"]["20m"]["items"][0]["hour"] == 7        # 12 UTC = 07 at UTC-5


def test_channel_anomaly_enhanced(tmp_path):
    conn = make_conn(tmp_path)
    # an old obs at a DIFFERENT hour extends the baseline span; "now" is open at AT's hour
    add(conn, tx_grid="EM12", rx_grid="FN19", snr=-6, ts=AT - 6 * 86400 - 7200, band="20m")
    for i in range(3):
        add(conn, tx_grid="EM12", rx_grid="FN19", snr=-6, ts=AT - 60 - i, band="20m")
    r = channel.anomaly(conn, origin="FN19", dest="EM12", at=AT, timezone="UTC")
    band = next(b for b in r["bands"] if b["band"] == "20m")
    assert band["deviation"] > 0          # open now, rarely open at this hour historically
