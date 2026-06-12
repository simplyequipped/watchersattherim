![watchers at the rim of a vast canyon](https://raw.githubusercontent.com/simplyequipped/watchersattherim/main/docs/images/watchersattherim.png)

## Watchers At The Rim

A receive-only FT8 propagation monitor for Reticulum/LXMF networks. Monitoring nodes passively decode FT8, extract per-path propagation observations, and report them to a collector over LXMF.

### Status

This project is beta status. More real world testing required.

### How it works

A monitor runs one [ft8mon](https://github.com/rtmrtmrtmrtm/ft8mon) process per band specified in the config file, reads its decodes, and turns them into propagation observations based on grid square locations and SNR signal reports:

```
ft8mon > parse decodes > callsign/grid cache > observations > batch > LXMF > collector
```

Each observation is a single `(tx_location, rx_location, snr, freq, time)` dataset. Two kinds of observations are produced:

- **direct** - a path between a remote transmitting station and the monitoring station
- **indirect** - a path between two remote stations

Observations are batched on an interval and sent to the collector in an LXMF message.

### Requirements

- Linux OS (Ubuntu, Fedora, Raspberry Pi OS)
- Python 3.10+
- `ft8mon` (compiled)
- HF audio source: a receiver feeding a sound card, or a `ft8mon` supported SDR
- A configured Reticulum (RNS) network with a path to the collector

### Installation

1. Install ft8mon build dependencies (fftw, libsndfile, portaudio)

    On Debian, Ubuntu, and Raspberry Pi OS:
    ```
    sudo apt update
    sudo apt install -y build-essential git libfftw3-dev libsndfile1-dev portaudio19-dev
    ```

    On Fedora:
    ```
    sudo dnf install -y gcc-c++ make git fftw-devel libsndfile-devel portaudio-devel
    ```

    **NOTE:** the base build supports sound card input. SDR backends require additional libraries and makefile edits (see the ft8mon README).

2. Build ft8mon

    ```
    git clone https://github.com/rtmrtmrtmrtm/ft8mon.git
    cd ft8mon
    make
    ```

    Clone the `https://github.com/mbroihier/ft8mon` fork instead for RTL-SDR v3 support.

    List sound-card device numbers with `./ft8mon -list`. Note the path to the compiled `ft8mon` binary, or add it to PATH.

3. Install WatchersAtTheRim monitor

    ```
    git clone https://github.com/simplyequipped/watchersattherim.git
    cd watchersattherim
    pip install .
    ```

    This installs the `watr` command and dependencies.

4. Configure Reticulum

    LXMF delivery requires a working Reticulum network. If you have not used Reticulum before, create a config and add at least one interface that can reach the collector. See the [Reticulum manual](https://markqvist.github.io/Reticulum/manual/). By default the monitor uses the standard `~/.reticulum` configuration.


### Configuration

Minimal configuration is required. See `examples/monitor.minimal.example.ini` and `examples/monitor.full.example.ini`.

```ini
[monitor]
grid = FN19

[receiver:20m]
freq = 14074000
card = 8

[collector]
address = <collector-lxmf-address-hex>
```

**NOTE:** if `ft8mon` is not on your `PATH`, set its location:
```ini
[ft8mon]
path = /home/pi/ft8mon/ft8mon
```

**Receivers.** Each `[receiver:NAME]` section is one ft8mon process, where `NAME` is the frequency band (ex. `20m`). Give **one** of `card`, `path`, or `input`. `freq` is the RF dial frequency in Hz: an SDR is tuned to it, and for an audio device (radio tuned externally) it is recorded as metadata.

| Input | Config Keys | `ft8mon` Command |
| :--- | :--- | :--- |
| Audio device | `card = 8` (or `8:0` to specify channel 0) | `-card 8 0` |
| Airspy HF+ | `input = airspy`, optional `serial` | `-card airspy <serial>,<mhz>` |
| RFspace SDR-IP / NetSDR / CloudIQ | `input = sdrip`, `ip` | `-card sdrip <ip>,<mhz>` |
| RFspace CloudSDR | `input = cloudsdr`, `ip` | `-card cloudsdr <ip>,<mhz>` |
| Apache / HPSDR | `input = hpsdr`, `ip` | `-card hpsdr <ip>,<mhz>` |
| WAV file | `path = file.wav` | `-card file <path>` |

An optional `args` value is appended to the `ft8mon` command.

### Usage

```
USAGE: watr [OPTIONS]          # watr-monitor is an equivalent alias

OPTIONS:
-c, --config PATH
    Path to the INI config file (default: monitor.ini)
-i, --identity
    Print this node's LXMF address (creating the identity if needed) and exit
--dry-run
    Print telemetry batches as JSON instead of sending them via LXMF
```

Verify decoding and parsing before you have a collector with a dry run:

```
watr -c monitor.ini --dry-run
```

In `--dry-run` the send interval is forced to 1 second so batches stream to stdout as they are produced (the config's `send_interval` is ignored).

Run live, reporting to the specified collector over LXMF:

```
watr -c monitor.ini
```

The monitor stores a persistent identity (and optional cache) under `storage.dir` (default `~/.watchersattherim`). Run `watr -i` to print its LXMF address, then send that to the collector operator so they can add it to the allowlist.

### Collector

The collector is a persistent process that receives telemetry from monitors over LXMF, validates LXMF addresses against an allowlist, stores observations in SQLite, and answers queries. It installs with the same package and provides the `watr-collector` command.

Print the collector's own LXMF address (monitors use it for their `[collector] address`):

```
watr-collector -i
```

Configure it with an INI file (see `examples/collector.minimal.example.ini` and `examples/collector.full.example.ini`). In the default `explicit` mode, monitors must be allowlisted by their LXMF address via the config file, or use `watr-collector --allow <hash>` / `--deny <hash>` / `--list-monitors` while the process is running:

```ini
[allowlist]
mode = explicit
allowed =
    <monitor-lxmf-address-hex>

[storage]
dir = ~/.watchersattherim/collector
```

Start the collector:

```
watr-collector -c collector.ini
```

The collector ingests over LXMF only. To also expose the read-only HTTP query API, set `http_api = true` (and optionally `bind` / `http_port`) under `[collector]` in the collector config file.

### Administration

Manage a running collector from its host with `watr-collector` (each command acts and exits):

| Command | Effect |
| :--- | :--- |
| `--list-monitors` | list known monitors and their allowed/denied state |
| `--allow <hash>` | allow a monitor to report |
| `--deny <hash>` | stop a monitor from reporting |
| `--block <hash>` | deny an address from querying |
| `--unblock <hash>` | re-allow an address to query |

Approved admins can also manage the collector remotely over LXMF from any LXMF client (such as [Sideband](https://github.com/markqvist/Sideband)). Add their LXMF addresses to the collector config:

```ini
[admin]
allowed =
    <admin-lxmf-address-hex>
```

They then send the collector a plain text message with one of:

| Command | Effect |
| :--- | :--- |
| `status` | collector statistics |
| `monitors` | list known monitors |
| `allow <hash>` / `deny <hash>` | allow / stop a monitor reporting |
| `blocked` | list query-blocked addresses |
| `block <hash>` / `unblock <hash>` | deny / re-allow an address from querying |
| `help` | command list |

### Querying

Observations can be queried over LXMF with the `watr-query` command, or over HTTP when the API is enabled:

```
watr-query <collector-address> path_query tx_grid=FN42 rx_grid=FN19 hours=4

curl "http://localhost:8080/api/v1/path?tx_grid=FN42&rx_grid=FN19&hours=4"
```

| Query | Parameters | HTTP endpoint |
| :--- | :--- | :--- |
| `path_query` | `tx_grid`, `rx_grid`, `hours` (default 4), `band` (optional) | `GET /api/v1/path` |
| `from_grid` | `grid`, `hours` (default 4) | `GET /api/v1/from` |
| `to_grid` | `grid`, `hours` (default 4) | `GET /api/v1/to` |
| `band_activity` | `band`, `hours` (default 1) | `GET /api/v1/band` |
| `monitor_list` | - | `GET /api/v1/monitors` |
| `monitor_info` | `address` (hex) | `GET /api/v1/monitors/<address>` |
| `stats` | - | `GET /api/v1/stats` |

Grids match by prefix, so a 4-character grid (`FN42`) also matches its 6-character subsquares (`FN42xx`). Each query returns matching observations plus a summary (count, SNR min/median/max, contributing monitors, direct/indirect split).

Every `watr-query` reply has the same outer shape: a protocol version `v`, the
`request_id` echoed back, an `ok` flag, and a `result` payload. The HTTP API returns
the same `result` but omits `v` and `request_id`. The shape of `result` depends on the
query.

`path_query`, `from_grid`, `to_grid`, and `band_activity` all return observations plus a summary:

```json
{
  "v": 1,
  "request_id": "a1b2c3d4",
  "ok": true,
  "result": {
    "observations": [
      {
        "monitor": "71260bdeece7a1f4c0d2",
        "observed_at": 1781282445,
        "mode": "FT8",
        "band": "40m",
        "freq_hz": 7074123,
        "tx_grid": "FN42", "tx_lat": 42.5, "tx_lon": -71.0,
        "rx_grid": "FN19", "rx_lat": 40.5, "rx_lon": -74.0,
        "snr_db": -9,
        "type": "direct"
      }
    ],
    "summary": {
      "count": 1,
      "snr_min": -9, "snr_max": -9, "snr_median": -9.0,
      "first_seen": 1781282445, "last_seen": 1781282445,
      "monitor_count": 1, "direct_count": 1, "indirect_count": 0
    }
  }
}
```

`monitor_list`:

```json
{
  "v": 1,
  "request_id": "a1b2c3d4",
  "ok": true,
  "result": {
    "monitors": [
      {
        "address": "71260bdeece7a1f4c0d2",
        "grid": "FN19",
        "lat": 40.5, "lon": -74.0,
        "last_seen": 1781282445,
        "sw_version": "watchersattherim-0.1.0",
        "allowed": true
      }
    ]
  }
}
```

`monitor_info`:

```json
{
  "v": 1,
  "request_id": "a1b2c3d4",
  "ok": true,
  "result": {
    "monitor": {
      "address": "71260bdeece7a1f4c0d2",
      "grid": "FN19",
      "lat": 40.5, "lon": -74.0,
      "first_seen": 1781200000,
      "last_seen": 1781282445,
      "sw_version": "watchersattherim-0.1.0",
      "allowed": true,
      "observations_24h": 482
    }
  }
}
```

`stats`:

```json
{
  "v": 1,
  "request_id": "a1b2c3d4",
  "ok": true,
  "result": {
    "total_observations": 12044,
    "observations_1h": 318,
    "observations_24h": 7651,
    "total_monitors": 3,
    "active_monitors": 2,
    "per_band_1h": { "20m": 210, "40m": 108 },
    "distinct_tx_grids_24h": 412,
    "distinct_rx_grids_24h": 6,
    "ingest": {
      "accepted": 12044,
      "duplicates": 37,
      "rejected_allowlist": 0,
      "rejected_timestamp": 2,
      "rejected_schema": 0
    },
    "queries_total": 51,
    "top_query_sources": [["c0ffee00112233445566", 40], ["deadbeef00112233aabb", 11]],
    "refreshed_at": 1781282450
  }
}
```

On error, `ok` is `false`, there is no `result`, and an `error_code` (integer) and
`error` (message) are included instead:

```json
{
  "v": 1,
  "request_id": "a1b2c3d4",
  "ok": false,
  "error_code": 2,
  "error": "unknown grid: ZZ99"
}
```

### Develop / Test

```
pip install -e '.[dev]'
pytest
```

### Acknowledgements

[ft8mon](https://github.com/rtmrtmrtmrtm/ft8mon) by Robert Morris, AB1HL<br>
[Reticulum](https://github.com/markqvist/Reticulum) and [LXMF](https://github.com/markqvist/LXMF) by Mark Qvist<br>
FT8 protocol by Steven Franke, Bill Somerville, and Joe Taylor (WSJT-X)<br>

