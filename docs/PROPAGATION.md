# Propagation Layer Specification

The propagation layer turns the collector's raw per-path observations into
queryable propagation intelligence: best-band advisories, reachability and
activity maps, historical patterns, and anomaly detection. It is a consumer of
collected data, never part of ingest.

```
monitors -> collector (raw observation store) -> propagation queries -> consumers
                                                  (channel/trend/map/...)  (modem, dashboards, ALE)
```

The propagation queries are additional commands in the collector's existing query
dispatch, so they are served over the same two transports, with the same
allowlist, rate limiting, and response envelope as the raw queries. The inference
logic lives in the `propagation` subpackage (pure functions over a database
connection); the `watr-propagation` CLI runs any of these commands against a
collector database read-only, for local debugging and snapshots.

## Transports and envelope

Every query is available over both:

- **HTTP**: `GET /api/v1/<route>?<params>`
- **LXMF**: a query message with a `command` and `params`

Both return the same `result`. Over LXMF the reply is wrapped:

```json
{ "v": 1, "request_id": "a1b2c3d4", "ok": true, "result": { } }
```

The HTTP API returns the same `result` without `v`/`request_id`:

```json
{ "ok": true, "result": { } }
```

On error, `ok` is `false`, there is no `result`, and an
`error_code` (integer) and `error` (message) are included:

```json
{ "v": 1, "request_id": "a1b2c3d4", "ok": false, "error_code": 2, "error": "unknown grid: ZZ99" }
```

Error codes: `0` success, `1` invalid command, `2` invalid params, `3` not
authorized, `4` internal error, `5` rate limited

## Common conventions

**Points (`origin`/`dest`).** Accept either a Maidenhead grid (`FN19`, any
precision) or a `lat,lon` pair (`40.5,-74.0`) in the same parameter. The
single-endpoint raw queries `from`/`to` take the point in a `grid` parameter that
likewise accepts a grid or `lat,lon`. Every result echoes each point as a resolved object so a consumer
always has coordinates and the resolved grid:

```json
"origin": { "grid": "FN19", "lat": 49.5, "lon": -77.0 }
```

**`window`.** A lookback duration string (`30m`, `2h`, `7d`).
Nowcast endpoints use short windows; raw and historical endpoints use 
longer ones. Defaults are per endpoint.

**`resolution`** (map/coverage). Cell size as `coarse` | `medium` | `fine`,
corresponding internally to Maidenhead grid field / square / subsquare. Default
`medium`. Cells are always Maidenhead grids; the named values keep the meaning
clear without grid-character arithmetic. The README documents the correspondence.

**`units`** (`km` default, or `mi`). Controls the unit for every distance in a result -
the `distance` field and the `radius` of `map`/`coverage` - and the result echoes a
top-level `units`. Applies to `channel`, `trend/path`, `trend/band`, `map`, and `coverage`.

**`timezone`** (trend and anomaly only). The hour-of-day buckets and the
per-hour-of-day anomaly baselines are computed in this zone. A query arg
`timezone` accepts an IANA name (`America/New_York`) or offset; when omitted it
falls back to the collector's `[propagation] default_timezone` (which defaults to
`UTC`). UTC is the universal default - amateur radio operates in UTC, and
propagation events are UTC-instant globally. The nowcast endpoints (`channel`,
`map`, `coverage`) do not bucket by hour-of-day, so `timezone` does not apply to
them. (Civil time is pragmatic but not the physically-correct frame; a future
`timezone=solar` mode would bucket by longitude-derived solar local time, the
frame propagation actually tracks. Deferred.)

**`bands` vs `band`.** Plurality encodes arity. `bands` (plural) is an optional
filter list (comma separated) where multiple are allowed (`channel`, `trend/*`,
`coverage`); omitted means all bands. `band` (singular) is exactly one where a
single band is required by the query shape (`map`, `trend/band/*`, the raw `band`
query) and carries a default of `40m`.

**Grid widening (channel only).** `channel` works from a short recent window, so
if a path has too little data at the requested precision it broadens the grid
match (subsquare -> square -> field) until it finds evidence, reports the precision
actually used as `match_precision`, and lowers `confidence` accordingly. Pass
`widen=false` to hold the exact precision. No other endpoint widens: `trend`
aggregates abundant history (breadth is controlled by the grid precision you
pass), and `map`/`coverage` report only cells that have data.

**Support-bounding.** `map` and `coverage` return only cells that have
observations. There is no interpolation into empty cells; the field simply fades
out where the network has no evidence.

**Reciprocity.** Receive-only data means inbound paths are observed. `channel` and
`coverage` advise outbound links by assuming the reverse path is comparable for
the purpose of whether a band is usable, smoothed by taking medians over many
observations. It is the load-bearing assumption of the system.

## Shared metrics

- **`quality`** (FT8) - median SNR mapped onto a fixed scale (-24 to +10 dB -> 0..1),
  clamped. Absolute and comparable across bands, times, and maps.
- **`median_snr_db`** - the raw median SNR in dB
- **`ref_snr_db`** (WSPR) - median SNR expressed at a shared reference power
  (`ref_power_dbm`, default 37 = 5 W): `snr + (ref_power - power)` per beacon. This
  de-confounds transmit power, so WSPR paths become comparable. Higher is better.
- **`median_power_dbm`** (WSPR) - median reported TX power in dBm.
- **`openness`** - fraction of time-slots in a period that had at least one decode
  (0..1). Path-scoped; the "how often is it open" measure.
- **`observations`** - count of observations behind a value.
- **`grids`** - distinct grids heard (spread); band-scoped activity only.
- **`distance`** - great-circle distance in the result's `units` (km or mi): the path
  length between origin and dest (`channel`, `trend/path`). The range from the center or
  fixed point to each cell (`map`/`coverage`). For `trend/band`, the median length of
  the paths heard in that bucket (a reach curve over time, and a selectable chart metric).
- **`bearing`** - initial great-circle bearing in degrees (0-360): origin to dest for
  path queries, or center/fixed point to each cell for `map` and `coverage`.
- **`confidence`** - 0-1 trust. Inputs vary by endpoint: `channel` and `coverage` use
  volume x freshness x widening-penalty. `trend` and `band` activity use volume
  only (historical data does not go stale, and they do not widen).
- **`deviation`** - current minus baseline (anomaly endpoints).

## Raw observation queries

Existing collector queries, harmonized to flat names and the shared `window`
param. They return stored observations (each carrying tx/rx grid and lat/lon) plus
a summary, except the registry/stat queries.

| Command | HTTP route | Args |
| :--- | :--- | :--- |
| `path` | `GET /api/v1/path` | `origin`, `dest`, `window`=2h, `band`? |
| `from` | `GET /api/v1/from` | `grid`\|`lat,lon`, `window`=2h |
| `to` | `GET /api/v1/to` | `grid`\|`lat,lon`, `window`=2h |
| `band` | `GET /api/v1/band` | `band`, `window`=1h |
| `monitors` | `GET /api/v1/monitors` | - |
| `monitor` | `GET /api/v1/monitors/<address>` | `address` |
| `stats` | `GET /api/v1/stats` | - |

## channel

**Best band to reach a target right now.** A point-to-point nowcast.

| Arg | Default | |
| :-- | :-- | :-- |
| `origin`, `dest` | - | grid or `lat,lon` |
| `window` | `30m` | recent evidence window |
| `bands` | all | optional filter list |
| `at` | now | reference time |
| `widen` | `true` | broaden grid match if sparse |
| `ref_power_dbm` | `37` | reference TX power (dBm) WSPR SNR is normalized to |
| `rank` | `ft8` | rank bands by `ft8` quality or `wspr` ref_snr_db |

```
GET /api/v1/channel?origin=FN19&dest=EM&window=30m
```
```json
{
  "v": 1, "request_id": "a1b2", "ok": true,
  "result": {
    "origin": { "grid": "FN19", "lat": 49.5, "lon": -77.0 },
    "dest":   { "grid": "EM",   "lat": 38.0, "lon": -90.0 },
    "distance": 1287.4, "units": "km", "bearing": 246.8,
    "at": 1781388000, "window": "30m",
    "ref_power_dbm": 37, "rank": "ft8", "ranked": ["40m", "20m"],
    "bands": {
      "40m": {
        "ft8":  { "quality": 0.71, "median_snr_db": -6, "confidence": 0.66,
                  "observations": 38, "reciprocal": 31, "last_seen": 1781387950, "match_precision": 4 },
        "wspr": { "ref_snr_db": -9, "median_snr_db": -16, "median_power_dbm": 30, "confidence": 0.40,
                  "observations": 12, "reciprocal": 12, "last_seen": 1781387900, "match_precision": 4 }
      },
      "20m": {
        "ft8":  { "quality": 0.40, "median_snr_db": -14, "confidence": 0.55,
                  "observations": 9, "reciprocal": 7, "last_seen": 1781387800, "match_precision": 4 },
        "wspr": null
      }
    }
  }
}
```

Each band carries an `ft8` and a `wspr` object - either can be `null` when that
mode has no data for the path. FT8's `quality`/`median_snr_db` give strength.
WSPR's `ref_snr_db` is the SNR each beacon would have produced at `ref_power_dbm`,
which makes paths comparable. `confidence` is how much to trust each.

`rank` selects the sort metric (`ft8` quality or `wspr` ref_snr_db); `ranked`
lists, best first, only the bands carrying that basis (a band with no data for the
rank mode stays in `bands` but is dropped from `ranked`). If the requested rank
mode has no data anywhere it falls back to the other, and the result's `rank`
field reports the basis actually used.

## channel/anomaly

**Is this path behaving abnormally right now,** versus its recent normal. Still a
now-signal; the baseline is only context. Same args as `channel` plus a recent
rolling `baseline`.

| Arg | Default | |
| :-- | :-- | :-- |
| (channel args) | | |
| `baseline` | `7d` | recent window for "normal at this hour" |

```
GET /api/v1/channel/anomaly?origin=FN19&dest=EM&baseline=7d
```
```json
{
  "result": {
    "origin": { "grid": "FN19", "lat": 49.5, "lon": -77.0 },
    "dest":   { "grid": "EM",   "lat": 38.0, "lon": -90.0 },
    "at": 1781390000, "window": "30m", "baseline": "7d",
    "bands": [
      {
        "band": "20m",
        "quality": 0.20, "median_snr_db": -17, "confidence": 0.7,
        "evidence": { "observations": 3, "reciprocal": 3, "last_seen": 1781389800, "match_precision": 4 },
        "baseline": { "openness": 0.62, "quality": 0.70, "median_snr_db": -7, "observations": 210 },
        "deviation": -0.57
      }
    ]
  }
}
```

The band carries the current `channel` estimate plus a `baseline` block (the norm
for this hour over the last 7 days) and a `deviation` (current presence vs.
baseline openness). Large negative deviation on a normally-open path is the
absorption signature of a disturbance; positive is an unusual opening.

## trend/path/{hour,month,year}

**The recurring pattern of a path over time** - the diurnal, seasonal, or
solar-cycle shape. The route is the bucket axis; optional same-named filters narrow
the data (you filter by an axis other than the one you bucket).

| Arg | Default | |
| :-- | :-- | :-- |
| `origin`, `dest` | - | grid or `lat,lon` |
| `bands` | all | optional filter list |
| `hour`/`month`/`year` | - | optional filters (not the bucket axis) |

```
GET /api/v1/trend/path/hour?origin=FN19&dest=IO93&month=6
```
```json
{
  "result": {
    "origin": { "grid": "FN19", "lat": 49.5, "lon": -77.0 },
    "dest":   { "grid": "IO93", "lat": 53.5, "lon": -1.5 },
    "unit": "hour",
    "filters": { "month": 6 },
    "bands": {
      "20m": {
        "items": [
          { "hour": 18, "openness": 0.66, "quality": 0.71, "median_snr_db": -6, "confidence": 0.80, "observations": 134 }
        ]
      }
    }
  }
}
```

Items run over the full axis (all 24 hours / 12 months / N years), including quiet
buckets, so it is a continuous curve. `openness` is the "how reliably open"
measure; scan for the band+bucket with the best openness/quality.

## trend/path/anomaly

**What happened on a path over a period** - a chronological hourly deviation
series, for event detection (a flare appears as a run of depressed hours at a
specific time). Baseline is the per-hour-of-day median across the window itself,
which a short event cannot bias.

| Arg | Default | |
| :-- | :-- | :-- |
| `origin`, `dest` | - | grid or `lat,lon` |
| `bands` | all | optional filter list |
| `window` | `7d` | span analyzed; `start`/`end` for a specific span |

```
GET /api/v1/trend/path/anomaly?origin=FN19&dest=EM&window=7d
```
```json
{
  "result": {
    "origin": { "grid": "FN19", "lat": 49.5, "lon": -77.0 },
    "dest":   { "grid": "EM",   "lat": 38.0, "lon": -90.0 },
    "unit": "hour", "window": "7d",
    "bands": {
      "20m": {
        "items": [
          { "time": 1781305200, "openness": 0.60, "baseline": 0.58, "deviation":  0.02, "observations": 14 },
          { "time": 1781388000, "openness": 0.05, "baseline": 0.61, "deviation": -0.56, "observations":  9 }
        ]
      }
    }
  }
}
```

One item per actual hour across the window (chronological, including empty hours),
each compared to the median for its hour-of-day. Plot `openness` (or `deviation`)
against `time`; a sustained dip is the event. Works when the event is a minority of
the window; widen `window` for a more robust baseline.

## trend/band/{hour,month,year}

**When a band is active over time** - the band-scoped dual of `trend/path/*`, not
tied to a path. Optionally limited to a region.

| Arg | Default | |
| :-- | :-- | :-- |
| `band` | `40m` | single band |
| `origin` + `radius` + `units` | none | optional region limit |
| `hour`/`month`/`year` | - | optional filters (not the bucket axis) |

```
GET /api/v1/trend/band/hour?band=40m&origin=FN19&radius=2000
```
```json
{
  "result": {
    "band": "40m",
    "unit": "hour",
    "units": "km",
    "region": { "origin": { "grid": "FN19", "lat": 49.5, "lon": -77.0 }, "radius_km": 2000 },
    "filters": {},
    "items": [
      { "hour": 2,  "observations": 320, "grids": 41, "distance": 2410.5, "quality": 0.55, "median_snr_db": -11 },
      { "hour": 14, "observations": 880, "grids": 96, "distance": 980.2, "quality": 0.66, "median_snr_db": -6 }
    ]
  }
}
```

`observations` (volume), `grids` (spread), `distance` (median reach of the paths
heard, in `units`), and `quality`/`median_snr_db` (strength) together show when the
band is hot and how far it reaches. No `openness` (meaningless band-wide). No single
synthetic "hotness" score; the components are exposed for the consumer to combine,
and any is selectable as the `watr-propagation --chart` metric.

## map

**Activity / conditions across an area right now,** aggregated across all
monitors. Origin-agnostic area for a single band.

| Arg | Default | |
| :-- | :-- | :-- |
| `origin` | - | center point (grid or `lat,lon`) |
| `radius` | `2000` | extent |
| `units` | `km` | `km` or `mi` |
| `band` | `40m` | single band |
| `mode` | `FT8` | `FT8` or `WSPR` |
| `resolution` | `medium` | cell size |
| `window` | `1h` | recent window |

```
GET /api/v1/map?origin=FN19&radius=2000&units=km&band=40m&resolution=medium
```
```json
{
  "result": {
    "origin": { "grid": "FN19", "lat": 49.5, "lon": -77.0 },
    "radius": 2000, "units": "km", "band": "40m", "mode": "FT8", "resolution": "medium", "window": "1h",
    "cells": [
      { "grid": "EM48", "lat": 38.5, "lon": -92.0, "distance": 540.2, "bearing": 261.0, "quality": 0.78, "median_snr_db": -7,  "observations": 142 },
      { "grid": "FN42", "lat": 42.5, "lon": -71.0, "distance": 480.1, "bearing": 74.0, "quality": 0.55, "median_snr_db": -11, "observations": 58 }
    ]
  }
}
```

Each cell carries absolute `quality` (fixed scale, so "good or bad" is comparable
across maps, not just hottest-relative), the raw `median_snr_db`, and
`observations` (activity volume). Cells with no data are omitted. A relative
hotspot view is a trivial client-side normalization of `quality`/`observations`.

## coverage

**Reachability from (or to) one point, as a field** - the channel estimate run per
cell. Specify one endpoint or the other (origin or dest).

| Arg | Default | |
| :-- | :-- | :-- |
| `origin` XOR `dest` | - | the fixed endpoint |
| `radius` | `2000` | extent |
| `units` | `km` | `km` or `mi` |
| `band` | best per cell | optional single band |
| `resolution` | `medium` | cell size |
| `window` | `30m` | recent window |
| `ref_power_dbm` | `37` | reference TX power for WSPR `ref_snr_db` |
| `rank` | `ft8` | sort cells by `ft8` quality or `wspr` ref_snr_db |

```
GET /api/v1/coverage?origin=FN19&radius=2000&units=km&resolution=medium
```
```json
{
  "result": {
    "origin": { "grid": "FN19", "lat": 49.5, "lon": -77.0 },
    "radius": 2000, "units": "km", "resolution": "medium", "window": "30m",
    "ref_power_dbm": 37, "rank": "ft8",
    "cells": [
      { "grid": "EM48", "lat": 38.5, "lon": -92.0, "distance": 540.2, "bearing": 261.0,
        "ft8":  { "band": "40m", "quality": 0.72, "median_snr_db": -7, "confidence": 0.66, "observations": 41 },
        "wspr": { "band": "30m", "ref_snr_db": -8, "median_snr_db": -15, "median_power_dbm": 30, "confidence": 0.42, "observations": 9 } },
      { "grid": "FN42", "lat": 42.5, "lon": -71.0, "distance": 480.1, "bearing": 74.0,
        "ft8":  { "band": "20m", "quality": 0.60, "median_snr_db": -10, "confidence": 0.50, "observations": 12 },
        "wspr": null }
    ]
  }
}
```

Each cell carries an `ft8` and a `wspr` object - the best band for that mode (or
`null`). With no `band`, each mode picks its own best band. Pass `band` to limit
results to a single band. `rank` sorts the cells (by `ft8` quality or `wspr`
ref_snr_db). Cells with no data are omitted (an absent cell means no evidence
of a path, not that the area is unreachable).
