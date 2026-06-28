# Local pipeline test

End-to-end test of the monitor -> LXMF -> collector -> query path over a real
Reticulum network, on one host, with no radio.

`run_pipeline.py` starts the real collector and the real monitor (the repo
versions, unmodified) in two isolated Reticulum instances linked by a loopback
TCP interface, feeds the monitor decodes through a stub `ft8mon`, and checks
that:

1. the collector starts and registers its LXMF delivery destination,
2. observations travel from the monitor to the collector database over LXMF
   (gzipped msgpack batches, decoded and ingested by the running collector),
3. a real `watr-query` round-trips and lists the monitor.

## Running

```
python tests/rns/run_pipeline.py
```

Needs RNS and LXMF importable (the repo's dev environment). Takes up to a couple
of minutes, most of it waiting for Reticulum path discovery between the
instances. Exit code 0 on success.

## What is here

- `reticulum-collector.config`, `reticulum-monitor.config`,
  `reticulum-query.config` are isolated Reticulum configs. `share_instance = No`
  keeps them off any default Reticulum instance on the host, and they talk only
  over a loopback `TCPServerInterface`/`TCPClientInterface` pair on port 52010.
- `collector.ini` and `monitor.ini.tmpl` are the app configs. The monitor
  template has the collector address and the stub ft8mon path filled in at run
  time.
- `fake_ft8mon` emits FT8 decode lines in ft8mon's stdout format. The monitor
  execs it in place of the real binary, so the real parse -> observations ->
  telemetry path runs with no SDR.
- `.run/` (git-ignored) holds everything the run generates: copied Reticulum
  configs, identities, the rendered monitor.ini, the collector database, and the
  process logs. It is recreated each run.

The test exercises code paths the unit suite cannot: payloads riding through
LXMF's field serialization, the collector listener decoding and ingesting, and
the query client decoding a reply.
