"""Tests for the collector core: storage, config, ingest, queries, stats."""

from pathlib import Path

import pytest

from watchersattherim.collector import queries, storage
from watchersattherim.collector.config import CollectorConfig, load
from watchersattherim.common.config import ConfigError
from watchersattherim.collector.ingest import ingest_batch
from watchersattherim.collector.stats import Stats

REPO = Path(__file__).resolve().parent.parent
NOW = 1_000_000
MON_A = b"\xaa" * 16
MON_B = b"\xbb" * 16


def db():
    return storage.connect(":memory:")


def obs(ts=NOW - 60, band="20m", freq=14074000, tx_grid="FN42", rx_grid="FN19",
        snr=-7, type="direct", tx_call=None, rx_call=None):
    return {
        "ts": ts, "mode": "FT8", "band": band, "freq": freq,
        "tx_lat": 42.5, "tx_lon": -71.5, "tx_grid": tx_grid,
        "rx_lat": 49.5, "rx_lon": -77.0, "rx_grid": rx_grid,
        "snr": snr, "type": type, "tx_call": tx_call, "rx_call": rx_call,
    }


def batch(observations, grid="FN19"):
    return {
        "v": 1,
        "monitor": {"grid": grid, "lat": 49.5, "lon": -77.0, "sw_version": "test"},
        "window": {"start": NOW - 60, "end": NOW},
        "observations": observations,
    }


# --- storage --------------------------------------------------------------

def test_insert_and_count():
    conn = db()
    storage.upsert_monitor(conn, MON_A, now=NOW)
    n = storage.insert_observations(conn, MON_A, [obs(), obs(freq=14075000)])
    assert n == 2 and storage.count_observations(conn) == 2


def test_dedup_identical_rows():
    conn = db()
    storage.upsert_monitor(conn, MON_A, now=NOW)
    storage.insert_observations(conn, MON_A, [obs()])
    again = storage.insert_observations(conn, MON_A, [obs(), obs(snr=-99 + 50)])
    # same (monitor, ts, freq, tx_grid) -> ignored despite different snr
    assert again == 0 and storage.count_observations(conn) == 1


def test_distinct_monitors_not_duplicates():
    conn = db()
    storage.upsert_monitor(conn, MON_A, now=NOW)
    storage.upsert_monitor(conn, MON_B, now=NOW)
    storage.insert_observations(conn, MON_A, [obs()])
    storage.insert_observations(conn, MON_B, [obs()])
    assert storage.count_observations(conn) == 2


def test_allowlist_flags():
    conn = db()
    assert storage.is_allowed(conn, MON_A) is False
    storage.set_allowed(conn, MON_A, True, now=NOW)
    assert storage.is_allowed(conn, MON_A) is True
    storage.set_allowed(conn, MON_A, False, now=NOW)
    assert storage.is_allowed(conn, MON_A) is False


def test_query_block_flags():
    conn = db()
    assert storage.is_query_blocked(conn, MON_A) is False
    storage.set_query_blocked(conn, MON_A, True, now=NOW)
    assert storage.is_query_blocked(conn, MON_A) is True
    assert len(storage.list_query_blocks(conn)) == 1
    storage.set_query_blocked(conn, MON_A, False, now=NOW)
    assert storage.is_query_blocked(conn, MON_A) is False


def test_prune_removes_old():
    conn = db()
    storage.upsert_monitor(conn, MON_A, now=NOW)
    storage.insert_observations(conn, MON_A, [
        obs(ts=NOW - 100 * 86400), obs(ts=NOW - 1 * 86400, freq=14075000),
    ])
    deleted = storage.prune(conn, retention_days=90, now=NOW)
    assert deleted == 1 and storage.count_observations(conn) == 1


# --- ingest ---------------------------------------------------------------

def test_ingest_rejects_non_allowlisted():
    conn = db()
    cfg = CollectorConfig(allowlist_mode="explicit")
    r = ingest_batch(conn, MON_A, batch([obs(), obs(freq=1)]), config=cfg, now=NOW)
    assert r.rejected_allowlist == 2 and r.accepted == 0
    assert storage.count_observations(conn) == 0


def test_ingest_open_mode_accepts():
    conn = db()
    cfg = CollectorConfig(allowlist_mode="open")
    r = ingest_batch(conn, MON_A, batch([obs()]), config=cfg, now=NOW)
    assert r.accepted == 1
    # open mode registers the monitor regardless of the allowlist
    assert conn.execute(
        "SELECT 1 FROM monitors WHERE address=?", (MON_A,)
    ).fetchone() is not None


def test_ingest_allowlisted_registers_monitor():
    conn = db()
    storage.set_allowed(conn, MON_A, True, now=NOW)
    cfg = CollectorConfig(allowlist_mode="explicit")
    r = ingest_batch(conn, MON_A, batch([obs()], grid="EN71"), config=cfg, now=NOW)
    assert r.accepted == 1
    row = conn.execute("SELECT grid FROM monitors WHERE address=?", (MON_A,)).fetchone()
    assert row["grid"] == "EN71"


def test_ingest_schema_rejects():
    conn = db()
    cfg = CollectorConfig(allowlist_mode="open")
    bad_lat = obs(); bad_lat["tx_lat"] = 200
    bad_snr = obs(freq=2); bad_snr["snr"] = 999
    bad_type = obs(freq=3); bad_type["type"] = "sideways"
    missing = obs(freq=4); del missing["band"]
    r = ingest_batch(conn, MON_A, batch([bad_lat, bad_snr, bad_type, missing]),
                     config=cfg, now=NOW)
    assert r.rejected_schema == 4 and r.accepted == 0


def test_ingest_timestamp_rejects():
    conn = db()
    cfg = CollectorConfig(allowlist_mode="open")
    r = ingest_batch(conn, MON_A, batch([
        obs(ts=NOW - 86400 - 100),        # too old
        obs(ts=NOW + 400, freq=14075000),  # too far future
        obs(ts=NOW - 30, freq=14076000),   # ok
    ]), config=cfg, now=NOW)
    assert r.rejected_timestamp == 2 and r.accepted == 1


def test_ingest_counts_duplicates():
    conn = db()
    cfg = CollectorConfig(allowlist_mode="open")
    ingest_batch(conn, MON_A, batch([obs()]), config=cfg, now=NOW)
    r = ingest_batch(conn, MON_A, batch([obs()]), config=cfg, now=NOW)
    assert r.accepted == 0 and r.duplicates == 1


# --- queries --------------------------------------------------------------

def _seed(conn):
    storage.upsert_monitor(conn, MON_A, grid="FN19", lat=49.5, lon=-77.0, now=NOW)
    storage.insert_observations(conn, MON_A, [
        obs(tx_grid="FN42", rx_grid="FN19", band="20m", snr=-5, freq=1),
        obs(tx_grid="FN42AB", rx_grid="FN19", band="20m", snr=-15, freq=2),
        obs(tx_grid="EM48", rx_grid="FN19", band="40m", snr=-9, freq=3, type="indirect"),
    ])


def test_path_query_with_grid_prefix():
    conn = db()
    _seed(conn)
    res = queries.path_query(conn, tx_grid="FN42", rx_grid="FN19", now=NOW, hours=4)
    # FN42 prefix matches both FN42 and FN42AB
    assert res["summary"]["count"] == 2
    assert res["summary"]["snr_min"] == -15 and res["summary"]["snr_max"] == -5
    assert res["summary"]["monitor_count"] == 1


def test_from_and_to_grid():
    conn = db()
    _seed(conn)
    assert queries.from_grid(conn, grid="FN42", now=NOW)["summary"]["count"] == 2
    assert queries.to_grid(conn, grid="FN19", now=NOW)["summary"]["count"] == 3


def test_band_activity():
    conn = db()
    _seed(conn)
    res = queries.band_activity(conn, band="40m", now=NOW, hours=4)
    assert res["summary"]["count"] == 1
    assert res["summary"]["indirect_count"] == 1


def test_monitor_list_and_info():
    conn = db()
    _seed(conn)
    ml = queries.monitor_list(conn, now=NOW)
    assert len(ml["monitors"]) == 1 and ml["monitors"][0]["grid"] == "FN19"
    info = queries.monitor_info(conn, address=MON_A.hex(), now=NOW)
    assert info["monitor"]["observations_24h"] == 3


# --- stats ----------------------------------------------------------------

def test_stats_snapshot():
    conn = db()
    _seed(conn)
    s = Stats(conn)
    snap = s.refresh(now=NOW)
    assert snap["total_observations"] == 3
    assert snap["observations_24h"] == 3
    assert snap["per_band_1h"] == {"20m": 2, "40m": 1}
    assert snap["distinct_tx_grids_24h"] == 3
    assert snap["active_monitors"] == 1


def test_stats_query_counts():
    conn = db()
    s = Stats(conn)
    s.record_query("aa")
    s.record_query("aa")
    s.record_query("bb")
    snap = s.refresh(now=NOW)
    assert snap["queries_total"] == 3
    assert snap["top_query_sources"][0] == ("aa", 2)


# --- config ---------------------------------------------------------------

def test_config_defaults(tmp_path):
    p = tmp_path / "collector.ini"
    p.write_text("[collector]\n")
    c = load(str(p))
    assert c.allowlist_mode == "explicit" and c.retention_days == 90
    assert c.reject_older_than_sec == 86400 and c.reject_future_skew_sec == 300
    assert c.http_api is False and c.http_port == 8080
    assert c.maintenance_hour == 3
    assert c.database_path.endswith("collector.db")
    assert c.database_path.endswith("/.watchersattherim/collector/collector.db")


def test_config_allowlist_and_durations(tmp_path):
    p = tmp_path / "collector.ini"
    p.write_text("""
[storage]
retention_days = 30
[allowlist]
mode = open
allowed = aabb, ccdd,  eeff
[ingest]
reject_older_than = 12h
[stats]
refresh_interval = 2m
""")
    c = load(str(p))
    assert c.allowlist_mode == "open"
    assert c.allowed == ("aabb", "ccdd", "eeff")
    assert c.retention_days == 30
    assert c.reject_older_than_sec == 12 * 3600
    assert c.stats_refresh_sec == 120


# --- command dispatch -----------------------------------------------------

from watchersattherim.collector import http
from watchersattherim.collector.commands import (
    CommandError, INVALID_COMMAND, INVALID_PARAMS, dispatch,
)


def test_dispatch_commands():
    conn = db()
    _seed(conn)
    s = Stats(conn)
    s.refresh(now=NOW)
    assert dispatch(conn, s, "from_grid", {"grid": "FN42"}, now=NOW)["summary"]["count"] == 2
    assert dispatch(conn, s, "monitor_list", {}, now=NOW)["monitors"]
    assert dispatch(conn, s, "stats", {}, now=NOW)["total_observations"] == 3


def test_dispatch_unknown_command():
    conn = db()
    with pytest.raises(CommandError) as e:
        dispatch(conn, Stats(conn), "nope", {}, now=NOW)
    assert e.value.code == INVALID_COMMAND


def test_dispatch_missing_param():
    conn = db()
    with pytest.raises(CommandError) as e:
        dispatch(conn, Stats(conn), "path_query", {"tx_grid": "FN42"}, now=NOW)
    assert e.value.code == INVALID_PARAMS


def test_dispatch_bad_address():
    conn = db()
    with pytest.raises(CommandError) as e:
        dispatch(conn, Stats(conn), "monitor_info", {"address": "zzzz"}, now=NOW)
    assert e.value.code == INVALID_PARAMS


# --- http routing ---------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/api/v1/path", "path_query"),
    ("/api/v1/from", "from_grid"),
    ("/api/v1/to", "to_grid"),
    ("/api/v1/band", "band_activity"),
    ("/api/v1/monitors", "monitor_list"),
    ("/api/v1/stats", "stats"),
])
def test_http_route_endpoints(path, expected):
    command, _ = http.route(path, {})
    assert command == expected


def test_http_route_monitor_info():
    command, params = http.route("/api/v1/monitors/aabb", {})
    assert command == "monitor_info" and params["address"] == "aabb"


def test_http_route_unknown():
    with pytest.raises(CommandError):
        http.route("/api/v1/nope", {})


# --- shipped example files ------------------------------------------------

def test_minimal_collector_example_loads():
    c = load(str(REPO / "examples/collector.minimal.example.ini"))
    assert c.allowlist_mode == "explicit" and c.allowed == ()
    assert c.http_api is False and c.retention_days == 90
    assert c.database_path.endswith("collector.db")


def test_maintenance_hour_out_of_range(tmp_path):
    p = tmp_path / "c.ini"
    p.write_text("[maintenance]\nhour = 99\n")
    with pytest.raises(ConfigError, match="hour must be 0-23"):
        load(str(p))


def test_full_collector_example_exercises_options():
    c = load(str(REPO / "examples/collector.full.example.ini"))
    assert c.http_api is True and c.bind == "127.0.0.1" and c.http_port == 9000
    assert c.retention_days == 30
    assert c.allowed == ("aabbccddeeff00112233445566778899",
                         "99887766554433221100ffeeddccbbaa")
    assert c.admin_allowed == ("00112233445566778899aabbccddeeff",)
    assert c.reject_older_than_sec == 12 * 3600
    assert c.reject_future_skew_sec == 120
    assert c.stats_refresh_sec == 120
    assert c.maintenance_hour == 4
