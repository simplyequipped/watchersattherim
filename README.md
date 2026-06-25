![watchers at the rim of a vast canyon](https://raw.githubusercontent.com/simplyequipped/watchersattherim/main/docs/images/watchersattherim.png)

## Watchers At The Rim

A receive-only FT8 and WSPR propagation monitor for Reticulum/LXMF networks. Monitoring nodes passively decode FT8 and WSPR, extract per-path propagation observations, and report them to a collector over LXMF.

### Status

This project is beta status. More real world testing required.

### How it works

A monitor runs one decoder process per configured receiver, [ft8mon](https://github.com/rtmrtmrtmrtm/ft8mon) for FT8 or [wsprmon](https://github.com/simplyequipped/wsprmon) (a streaming wrapper around WSJT-X's `wsprd`) for WSPR, reads its decodes, and turns them into propagation observations based on grid square locations and SNR signal reports. WSPR observations also carry the transmitter's reported power:

```
ft8mon / wsprmon > parse decodes > observations > batch > LXMF > collector
```

Multiple receivers can share one SDR: [sdrfanout](https://github.com/simplyequipped/sdrfanout) owns the radio, fans it out to a stream per receiver, and each decoder reads its own stream, so a single SDR can run FT8 and WSPR at once. Configure the radio in the `[sdr]` section of the config file and set `sdr = true` on each receiver that is part of the SDR fan-out.

Each observation is a single `(tx_location, rx_location, snr, freq, time)` dataset. Two kinds of observations are produced:

- **direct**, a path between a remote transmitting station and the monitoring station
- **indirect**, a path between two remote stations

Observations are batched on an interval and sent to the collector in an LXMF message.

### Requirements

- Linux OS (Ubuntu, Fedora, Raspberry Pi OS)
- Python 3.10+
- `ft8mon`/`wsprmon`/`wsprd`/`sdrfanout` (compiled)
- HF audio source: an audio device (transceiver), a `ft8mon`/`wsprmon` supported SDR, or any SoapySDR device shared via `sdrfanout`
- A configured Reticulum (RNS) network with a path to the collector

### Installation

The install script installs system dependencies, clones and builds the
receivers (ft8mon, wsprd, wsprmon, sdrfanout), and installs `watchersattherim` into a virtualenv:

```bash
git clone https://github.com/simplyequipped/watchersattherim.git
cd watchersattherim  
  
# install monitor only
./install.sh monitor
# install monitor with a full example config file
./install.sh monitor --config
# install monitor with a full example config file and a system service
./install.sh monitor --service  
  
# install collector only
./install.sh collector
# install collector with a full example config file
./install.sh collector --config
# install collector with a full example config file and a system service
./install.sh collector --service
```

#### Manual installation

1. Install build dependencies (fftw, libsndfile, portaudio, soapysdr)

    Debian, Ubuntu, and Raspberry Pi OS:
    ```
    sudo apt update
    sudo apt install -y build-essential git libfftw3-dev libsndfile1-dev portaudio19-dev libsoapysdr-dev
    ```

    Fedora:
    ```
    sudo dnf install -y gcc-c++ make git fftw-devel libsndfile-devel portaudio-devel SoapySDR-devel
    ```

    **NOTE:** `ft8mon` and `wsprmon` base builds support sound card input. Their own SDR backends require additional libraries and makefile edits (see their respective READMEs). `sdrfanout` uses SoapySDR instead, which works with any SoapySDR device. Installing `libsoapysdr-dev` typically pulls the full device-module bundle (rtlsdr/hackrf/airspy/... ~30-40 MB) via recommended packages, so any supported SDR works out of the box. To install only what you need, add `--no-install-recommends` and the specific `soapysdr<ver>-module-<device>` packages.

2. Build ft8mon

    ```
    git clone https://github.com/simplyequipped/ft8mon.git
    cd ft8mon
    make
    ```

    List sound-card device numbers with `./ft8mon -list`. Note the path to the compiled `ft8mon` binary, or add it to PATH.

3. Build wsprd

    ```
    git clone https://github.com/simplyequipped/wsprd.git
    cd wsprd
    make
    ```

    Note the path to the compiled `wsprd` binary, or add it to PATH.

4. Build wsprmon

    ```
    git clone https://github.com/simplyequipped/wsprmon.git
    cd wsprmon
    make
    ```

    Note the path to the compiled `wsprmon` binary, or add it to PATH.

5. Build sdrfanout (only needed to share an SDR across receivers)

    ```
    git clone https://github.com/simplyequipped/sdrfanout.git
    cd sdrfanout
    make
    ```

    Note the path to the compiled `sdrfanout` binary, or add it to PATH.

6. Install WatchersAtTheRim package

    ```
    git clone https://github.com/simplyequipped/watchersattherim.git
    cd watchersattherim
    pip install .
    ```

7. Configure Reticulum

    LXMF delivery requires a working Reticulum network. If you have not used Reticulum before, create a config and add at least one interface that can reach the collector. See the [Reticulum manual](https://markqvist.github.io/Reticulum/manual/). By default the monitor uses the standard `~/.reticulum/config` configuration.


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

**NOTE:** if `ft8mon` / `wsprmon` are not on `PATH`, set their locations:
```ini
[ft8mon]
path = /home/pi/ft8mon/ft8mon

[wsprmon]
path = /home/pi/wsprmon/wsprmon
wsprd_path = /home/pi/wsprd/wsprd   # optional, wsprmon finds wsprd on PATH if unset
```

**Receivers.** Each `[receiver:NAME]` section is one decoder process. `NAME` is a unique id; `band` defaults to it (ex. `20m`). `mode` is `ft8` (default) or `wspr`, and `enabled = false` keeps a section configured but not running. Give **one** of `card`, `path`, `input`, or `sdr`. `freq` is the RF dial frequency in Hz: an SDR is tuned to it, and for an audio device (radio tuned externally) it is recorded as metadata.

| Input | Config Keys | `ft8mon`/`wsprmon` Command |
| :--- | :--- | :--- |
| Audio device | `card = 8` (or `8:0` to specify channel 0) | `-card 8 0` |
| Airspy HF+ | `input = airspy`, optional `serial` | `-card airspy <serial>,<mhz>` |
| RFspace SDR-IP / NetSDR / CloudIQ | `input = sdrip`, `ip` | `-card sdrip <ip>,<mhz>` |
| RFspace CloudSDR | `input = cloudsdr`, `ip` | `-card cloudsdr <ip>,<mhz>` |
| Apache / HPSDR | `input = hpsdr`, `ip` | `-card hpsdr <ip>,<mhz>` |
| WAV file | `path = file.wav` | `-card file <path>` |
| Shared SDR (sdrfanout) | `sdr = true` (radio configured in `[sdr]`) | `-card stream <fifo>` |

An `input` SDR is opened directly by its one decoder, so it cannot be shared. To run more than one receiver off a single SDR, use `sdr = true` instead (see **Shared SDR** below).

An optional `args` value is appended to the decoder command.

Two optional per-receiver settings:

| Key | Default | Effect |
| :--- | :--- | :--- |
| `min_decode_snr` | `-25` (ft8), `-30` (wspr) | Drop decodes below this SNR before they become observations |
| `restart_after_silent` | off | Restart this receiver if it produces no decodes for this long (a duration, ex. `5m`) |

**Shared SDR.** An `[sdr]` section configures one physical SDR (via SoapySDR). Every receiver with `sdr = true` is a channel of it. The monitor launches `sdrfanout`, which tunes the radio once, digitally downconverts each receiver's `freq` to its own stream, and feeds each decoder. Device settings live in `[sdr]` (they apply to the whole radio) and only `freq` is per receiver.

```ini
[sdr]
driver = hackrf          # SoapySDR device name

[receiver:40m-ft8]
mode = ft8
band = 40m
sdr  = true
freq = 7074000

[receiver:40m-wspr]
mode = wspr
band = 40m
sdr  = true
freq = 7038600
```

By default `rate`, `center`, and `gain` are auto. When `rate = auto`, `sdrfanout` picks the lowest sample rate that spans every channel. Set `[sdr] path` if `sdrfanout` is not on `PATH`.

### Usage

```
USAGE: watr [OPTIONS]          # watr-monitor is an equivalent alias

OPTIONS:
-c, --config PATH
    Path to the INI config file. If omitted, looks for ./monitor.ini, then
    ~/.watchersattherim/monitor.ini (collector: ./collector.ini, then
    ~/.watchersattherim/collector/collector.ini), and errors if neither exists.
-i, --identity
    Print this node's LXMF address (creating the identity if needed) and exit
-v, --verbose
    Echo decodes to stdout, each prefixed with its mode (FT8 / WSPR) so interleaved
    receivers are easy to tell apart. By default only decodes kept as observations
    are shown (mirrors what reaches the collector). Set [monitor] debug = true for
    the raw decode firehose (weak/below-SNR decodes, unrendered message types, etc.).
--dry-run
    Print telemetry batches as JSON instead of sending them via LXMF
```

Verify decoding and parsing before you have a collector with a dry run:

```
watr -c monitor.ini --dry-run
```

In `--dry-run` the send interval is forced to 15 seconds so batches stream to stdout as they are produced (the config's `send_interval` is ignored).

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

Manage a running collector from its host with `watr-collector`:

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
watr-query <collector-address> path origin=FN42 dest=FN19 window=2h

curl "http://localhost:8080/api/v1/path?origin=FN42&dest=FN19&window=2h"
```

These are the raw observation queries (recent spots plus a summary):

| Query | Parameters | HTTP endpoint |
| :--- | :--- | :--- |
| `path` | `origin`, `dest`, `window` (default 2h), `band` (optional) | `GET /api/v1/path` |
| `from` | `grid` or `lat,lon`, `window` (default 2h) | `GET /api/v1/from` |
| `to` | `grid` or `lat,lon`, `window` (default 2h) | `GET /api/v1/to` |
| `band` | `band`, `window` (default 1h) | `GET /api/v1/band` |
| `monitors` | - | `GET /api/v1/monitors` |
| `monitor` | `address` (hex) | `GET /api/v1/monitors/<address>` |
| `stats` | - | `GET /api/v1/stats` |

`origin`/`dest` accept a Maidenhead grid or a `lat,lon` pair; `window` is a duration string
(`30m`, `2h`, `7d`). Grids match by prefix, so a 4-character grid (`FN42`) also matches its
6-character subsquares (`FN42xx`). Each query returns matching observations plus a summary
(count, SNR min/median/max, contributing monitors, direct/indirect split).

On top of these, the **propagation queries** turn observations into intelligence -
`channel` (best band to a target now), `trend/*` (patterns over time + anomaly detection),
`map` (activity over an area), and `coverage` (reachability over an area). They share this
same `watr-query`/HTTP/LXMF surface and envelope. See
[docs/PROPAGATION.md](docs/PROPAGATION.md) for the full specification.

### Interpreting Query Results

The network only ever sees what has been **decoded**, so every result is authoritative about
*what was decodable*, not about absolute channel availability or what your particular
station can work. Read the metrics with that scope in mind:

- **Authoritative (direct measurements):** `distance` and `bearing` (geometry),
  `median_snr_db` / `quality` (decode strength; `quality` is just SNR rescaled to 0-1), and
  `confidence` (how much fresh data backs the number, a *trust* signal, not a
  channel-goodness signal). For WSPR, `ref_snr_db` is *more* authoritative still: because
  WSPR carries the transmit power, it normalizes power out (`channel`/`coverage` report it
  per mode, alongside FT8), giving a path metric the ERP confound below does not touch.
- **Activity-related (the crowd + coverage, not pure channel):** `observations`, `grids`,
  and `openness` (a decode-rate, reliable where activity is dense, but a quiet path with no
  decodes is not the same as a closed band). Use these for "how busy/widespread," not "how
  good."
- **Most robust:** `deviation` (anomaly), a *relative* metric, so the shared activity and
  coverage biases cancel out. It is the honest answer to "is something unusual right now."

Two reading tips: on a **fixed path** (`channel`, `trend/path`), watch SNR/`quality` -
conditions move it. Across a **whole band** (`trend/band`), watch `distance`, the network
self-selects decodable signals, so SNR clusters and *reach* carries the diurnal swing.

Caveats that apply throughout: the data is receive-only, so `channel`/`coverage` infer your
outbound link from observed inbound paths (**reciprocity**). FT8 SNR includes the other station's
power and antenna (**ERP**), which a median smooths but does not remove (WSPR's `ref_snr_db` does,
by normalizing to a reference power). A spatial result
with no data for an area means a **blind spot**, not a closed band. Converting any of this to
"can *my* station reach X" is a link-budget step that needs your power/antenna and is an
estimate, not an authoritative output.

What it is authoritative per endpoint:

| Endpoint | Authoritative | Not |
| :--- | :--- | :--- |
| `path`/`from`/`to`/`band` | the actual records decoded, and stats over them | anything beyond what was decoded |
| `channel` | a robust, network-derived SNR for a path now | whether *you* can work it, absolute reach |
| `channel/anomaly` | whether now differs from the recent normal for this hour | the cause of the difference |
| `trend/path/*` | a path's strength/decode-rate shape over time | absolute availability on quiet paths |
| `trend/band/*` | a band's activity, spread, and reach over time | channel quality from `observations` alone |
| `map` | strength/activity per area, as heard by the monitor(s) | areas with no data (blind, not closed) |
| `coverage` | areas with decodable paths to/from a point, and their strength | absence as "no reach" (it is no evidence) |

Every `watr-query` reply has the same outer shape: a protocol version `v`, the
`request_id` echoed back, an `ok` flag, and a `result` payload. The HTTP API returns
the same `result` but omits `v` and `request_id`. The shape of `result` depends on the
query.

`path`, `from`, `to`, and `band` all return observations plus a summary:

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

`monitors`:

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

`monitor`:

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

- [ft8mon](https://github.com/rtmrtmrtmrtm/ft8mon) by Robert Morris
- [wsprd](https://wsjt.sourceforge.io/) WSPR decoder from WSJT-X via [https://github.com/pavel-demin/wsprd](https://github.com/pavel-demin/wsprd)
- [Reticulum](https://github.com/markqvist/Reticulum) and [LXMF](https://github.com/markqvist/LXMF) by Mark Qvist
- FT8 protocol by Steven Franke, Bill Somerville, and Joe Taylor
- Claude AI contributed to this project

