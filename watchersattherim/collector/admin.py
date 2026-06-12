"""Plain-text admin command handler for use over LXMF (e.g. from Sideband).

An admin sends a short text message to the collector ("status", "allow <hash>",
…) and gets a text reply. Authorization is checked by the listener against the
admin allowlist before this is called; the handler itself just parses and acts.
"""

from __future__ import annotations

from . import storage

HELP = (
    "watchersattherim collector admin commands:\n"
    "  status            collector statistics\n"
    "  monitors          list known monitors\n"
    "  allow <hash>      allow a monitor to report\n"
    "  deny <hash>       stop a monitor from reporting\n"
    "  blocked           list query-blocked addresses\n"
    "  block <hash>      deny an address from querying\n"
    "  unblock <hash>    re-allow an address to query\n"
    "  help              this message"
)


def _addr(arg: str) -> bytes:
    return bytes.fromhex(arg)


def _status(stats) -> str:
    s = stats.snapshot()
    if not s:
        return "no stats yet"
    top = s.get("top_query_sources", [])
    lines = [
        "collector status:",
        f"  observations: {s['total_observations']} "
        f"(1h {s['observations_1h']}, 24h {s['observations_24h']})",
        f"  monitors: {s['active_monitors']} active / {s['total_monitors']} total",
        f"  grids 24h: tx {s['distinct_tx_grids_24h']}, rx {s['distinct_rx_grids_24h']}",
        f"  queries: {s.get('queries_total', 0)}",
    ]
    if top:
        lines.append("  top query sources:")
        for src, n in top[:5]:
            lines.append(f"    {src}  {n}")
    ing = s.get("ingest", {})
    lines.append(
        f"  ingest: +{ing.get('accepted', 0)} dup {ing.get('duplicates', 0)} "
        f"rej(allow/ts/schema) {ing.get('rejected_allowlist', 0)}/"
        f"{ing.get('rejected_timestamp', 0)}/{ing.get('rejected_schema', 0)}"
    )
    return "\n".join(lines)


def _monitors(conn) -> str:
    rows = storage.list_monitors(conn)
    if not rows:
        return "no monitors known"
    out = ["monitors:"]
    for r in rows:
        flag = "allowed" if r["allowed"] else "denied"
        out.append(f"  {r['address'].hex()}  {flag}  grid={r['grid'] or '-'}")
    return "\n".join(out)


def _blocked(conn) -> str:
    rows = storage.list_query_blocks(conn)
    if not rows:
        return "no query-blocked addresses"
    return "query-blocked:\n" + "\n".join(f"  {r['address'].hex()}" for r in rows)


def handle_admin_command(conn, stats, text: str, *, now: int) -> str:
    parts = (text or "").strip().split()
    if not parts:
        return HELP
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None

    try:
        if cmd in ("status", "stats"):
            return _status(stats)
        if cmd in ("monitors", "list"):
            return _monitors(conn)
        if cmd == "blocked":
            return _blocked(conn)
        if cmd in ("allow", "deny") and arg:
            storage.set_allowed(conn, _addr(arg), cmd == "allow", now=now)
            return f"{'allowed' if cmd == 'allow' else 'denied'} {arg}"
        if cmd in ("block", "block-query") and arg:
            storage.set_query_blocked(conn, _addr(arg), True, now=now)
            return f"query-blocked {arg}"
        if cmd in ("unblock", "unblock-query") and arg:
            storage.set_query_blocked(conn, _addr(arg), False, now=now)
            return f"query-unblocked {arg}"
        if cmd in ("help", "?", "commands"):
            return HELP
    except ValueError:
        return f"invalid address: {arg}"

    return f"unknown or incomplete command: {text.strip()}\n\n{HELP}"
