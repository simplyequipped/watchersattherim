## Changelog

### Version 0.4.1

- added a `[blacklist]` config section with `grids`, `callsigns`, and `freqs` lists

### Version 0.4.0

- added `sdrfanout` support for sharing one SDR across multiple receivers (ex. FT8 + WSPR)
- added `[sdr]` section to monitor config
- added per-receiver `sdr = true` to indicate a channel off the shared SDR configured in `[sdr]`
- added per-receiver `min_decode_snr` (default -25 ft8 / -30 wspr)
- added `[monitor] debug` (default false)
- added a mode column (FT8 / WSPR) to `-v` output
- added `sdrfanout` build and the SoapySDR dependency to `install.sh`
- changed single `restart_after_silent_cycles` to per-receiver `restart_after_silent` (duration like `5m`, default off)
- changed `-v` to only output decodes kept as observations (use `[monitor] debug=true` to see all decodes)
- changed startup messages to not show subprocess command (use `[monitor] debug=true` to see subprocess commands)
- changed default monitor config lookup: `./monitor.ini` then `~/.watchersattherim/monitor.ini`
- changed default collector config lookup: `./collector.ini` then `~/.watchersattherim/collector/collector.ini`
- changed `install.sh --config` to write config at the default path/filename to allow auto-discovery without specifying config
- changed config `[collector] send_interval` default from 60 to 120 seconds
- fixed telemetry `sw_version` which was hardcoded to 0.1.0
- updated example monitor config
- updated README.md

### Version 0.3.1

- fixed `wsprmon` receivers aborting under a systemd service due to missing wav file write permissions in default service working directory
- updated monitor and collector systemd service templates

### Version 0.3.0

- added WSPR support via `wsprmon`
- added per-receiver `mode` (ft8 | wspr), `band`, and `enabled` keys to monitor config
- added `[wsprmon]` section to monitor config file (`path`, `wsprd_path`)
- added `power_dbm` to telemetry handling collector observations table schema
- added WSPR `ref_snr_db` (SNR normalized to a reference power) to `channel`/`coverage`
- added `ref_power_dbm` and `rank` parameters to `channel`/`coverage`
- added `mode` parameter to `map` query
- added per-mode SNR normalization floor
- added `install.sh` installer with `--config` and `--service` options
- added systemd service files for monitor and collector
- changed monitor receiver sections: `NAME` is a unique id (`band` defaults to `NAME`)
- changed `channel`/`coverage` result structure to add `ft8`/`wspr` objects
- updated example config files
- updated README.md and `docs/PROPAGATION.md` for WSPR

### Version 0.2.0

- added propagation submodule
- added propagation queries to collector query handling via HTTP and LXMF
- added `[propagation]` section to collector config file
- added `watr-propagation` entry point CLI
- added `docs/PROPAGATION.md`
- added suppression of empty batch LXMF message (`[collector] send_empty_batches` in collector config)
- added watchdog to restart ft8mon process after no decodes (`[ft8mon] restart_after_silent_cycles` in monitor config)
- changed multiple query command names to simple nouns
- changed lookback parameter to `window` for multiple endpoints
- changed `ft8mon` subprocess to separate stderr from parsed stdout
- improved `ft8mon` output parser error handling
- updated `watr-query` entry point CLI with propagation commands
- updated README.md
- updated example monitor config file
- updated example collector config file
- fixed `RR73` parsed as a grid

### Version 0.1.0

- monitor: receive-only FT8 propagation monitor wrapping `ft8mon`
- collector: LXMF ingest, SQLite store, queries, retention, admin
- LXMF and HTTP query surface
- NomadNet micron pages
