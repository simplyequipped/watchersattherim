# NomadNet pages

Dynamic [NomadNet](https://github.com/markqvist/NomadNet) pages that serve the
collector's data over Reticulum as [micron](https://github.com/markqvist/NomadNet)
pages (spec §9). Each `.mu` is an executable Python script that queries the
collector's SQLite database directly and prints micron markup.

Pages: `index`, `path` (grid→grid), `from`, `to`, `band`, `monitors`, `about`.
Shared helpers (DB access, query SQL, micron rendering) live in `_watr.py`.

## Deploy

Copy the pages and `_watr.py` into your NomadNet node's pages directory and make
the `.mu` files executable:

```
cp *.mu _watr.py ~/.nomadnetwork/storage/pages/
chmod +x ~/.nomadnetwork/storage/pages/*.mu
```

They are stdlib-only (`sqlite3`), so they need no virtualenv — NomadNet runs each
page with `#!/usr/bin/env python3`.

If the collector's database is not at the default
`~/.watchersattherim/collector/collector.db`, edit `DB_PATH` at the top of
`_watr.py` (NomadNet does not pass arbitrary environment variables to pages, so
the location must be set in the file).

The pages open the database read-only, so they can run safely alongside a live
collector.

## Notes

These follow NomadNet's documented micron syntax and request-variable mechanism
(`field_<name>` for form fields, `var_<name>` for link variables), but have not
yet been rendered in a live NomadNet browser — validate appearance there.
