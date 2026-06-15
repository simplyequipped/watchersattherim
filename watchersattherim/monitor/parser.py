"""Parse ft8mon stdout into structured decodes and classified messages.

This module is deliberately self-contained (stdlib only) so it can be tested in
isolation against captured ft8mon output, with no LXMF/RNS/SQLite dependencies.

Two layers:

1. ``parse_line`` - turn one line of ft8mon stdout into a ``Decode`` (the fixed
   numeric columns + raw message text), or ``None`` for non-decode lines
   (startup spew, the ``HH:MM:SS decodes: N`` cycle headers, etc.).

2. ``classify`` - turn a decode's message text into a ``Message`` with the
   callsigns, grid, and report broken out, following the FT8 message structure
   documented in the WSJT-X QEX paper and kgoba/ft8_lib.

Message semantics (confirmed against the WSJT-X QEX paper and ft8_lib message.h):
a standard message ``CALL_TO CALL_DE EXTRA`` is *transmitted by* CALL_DE (the
second callsign - "DE" = "from"). A grid in EXTRA is the transmitter's grid. A
signal report in EXTRA is the report CALL_DE is giving CALL_TO, i.e. how well
CALL_DE heard CALL_TO. See ``observations.py`` for how that maps to paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional

# ---------------------------------------------------------------------------
# Line parsing
# ---------------------------------------------------------------------------

# ft8mon decode line, e.g.:
#   023645  -7 147  1.34  327.6 KE2CUR KF9UG  -02
# columns: HHMMSS  SNR  correct_bits  DT  audio_hz  message
# The leading HHMMSS is six bare digits, which cleanly distinguishes a decode
# line from the cycle header (which starts "HH:MM:SS" with colons).
_DECODE_RE = re.compile(
    r"^(?P<ts>\d{6})\s+"
    r"(?P<snr>-?\d+)\s+"
    r"(?P<bits>\d+)\s+"
    r"(?P<dt>-?\d+\.\d+)\s+"
    r"(?P<freq>\d+\.\d+)\s+"
    r"(?P<msg>.*)$"
)


@dataclass(frozen=True)
class Decode:
    """One raw ft8mon decode line."""

    hhmmss: str          # "023645", UTC, start of the 15 s slot
    snr: int             # dB, monitor's reported SNR for this decode
    dt: float            # seconds, time offset of the signal in the slot
    freq: float          # Hz, audio frequency
    message: str         # raw message text, whitespace-trimmed
    bits: int            # ft8mon's corrected-bits count (decoder-internal)


def parse_line(line: str) -> Optional[Decode]:
    """Parse one line of ft8mon stdout. Returns ``None`` for non-decode lines."""
    m = _DECODE_RE.match(line)
    if not m:
        return None
    return Decode(
        hhmmss=m["ts"],
        snr=int(m["snr"]),
        dt=float(m["dt"]),
        freq=float(m["freq"]),
        message=m["msg"].strip(),
        bits=int(m["bits"]),
    )


def parse_stream(lines: Iterator[str]) -> Iterator[Decode]:
    """Yield a ``Decode`` for every decode line in an iterable of lines."""
    for line in lines:
        d = parse_line(line)
        if d is not None:
            yield d


# ---------------------------------------------------------------------------
# Message classification
# ---------------------------------------------------------------------------

# A callsign: optional prefix chars, an area digit, a suffix, optional /portable.
# Matches K1JT, W3GO, 3B8WW, VA6RCN, W1AW/7, LK6ZOP/R. Deliberately does NOT
# match Maidenhead grids (EN71) or reports (73, R-05), so it discriminates a
# CQ modifier (DX, POTA, NA - no digit) from the caller that follows it.
_CALL_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}(/[A-Z0-9]{1,4})?$")

# 4- or 6-character Maidenhead locator.
_GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$")

# Signal report (carries an SNR) e.g. -02, +06, R-03, R+15.
_REPORT_NUM_RE = re.compile(r"^R?(?P<val>[-+]\d{1,2})$")
# Acknowledgement tokens that occupy the report slot but carry no SNR.
_ACK_TOKENS = frozenset({"RRR", "RR73", "73", "RR"})

# An unrendered non-standard message ft8mon prints verbatim, e.g. "i3=0 n3=1".
_I3N3_RE = re.compile(r"^i3=\d+ n3=\d+$")


class Kind(Enum):
    CQ = "cq"              # CQ [modifier] CALL_DE [GRID]
    STANDARD = "standard"  # CALL_TO CALL_DE [GRID|REPORT]
    NONSTD = "nonstd"      # involves a hashed <...> callsign
    REJECT = "reject"      # free text / telemetry / contest / unparseable


@dataclass(frozen=True)
class Message:
    kind: Kind
    call_to: Optional[str] = None      # station being called (None for CQ)
    call_de: Optional[str] = None      # transmitting station
    grid: Optional[str] = None         # transmitter's grid, if present
    report_db: Optional[int] = None    # numeric SNR report, if present
    cq_modifier: Optional[str] = None  # "DX", "POTA", ... for CQ messages
    to_hashed: bool = False            # call_to was a <...> hashed call
    de_hashed: bool = False            # call_de was a <...> hashed call
    reject_reason: Optional[str] = None
    raw: str = ""


def _is_call(tok: str) -> bool:
    return bool(_CALL_RE.match(tok))


def _is_call_or_hash(tok: str) -> bool:
    return tok.startswith("<") or _is_call(tok)


def _is_grid(tok: str) -> bool:
    # RR73 satisfies the locator pattern but is the "roger + 73" sign-off, not a
    # grid; exclude the ack tokens so they are never read as a transmitter location.
    return tok not in _ACK_TOKENS and bool(_GRID_RE.match(tok))


def _report_db(tok: str) -> Optional[int]:
    m = _REPORT_NUM_RE.match(tok)
    return int(m["val"]) if m else None


def classify(message: str) -> Message:
    """Classify a decode's message text into structured fields."""
    msg = message.strip()
    tokens = msg.split()

    if not tokens:
        return Message(Kind.REJECT, reject_reason="empty", raw=msg)

    if _I3N3_RE.match(msg):
        return Message(Kind.REJECT, reject_reason="unrendered_type", raw=msg)

    # --- CQ / QRZ -----------------------------------------------------------
    if tokens[0] in ("CQ", "QRZ"):
        rest = tokens[1:]
        # Leading non-callsign tokens after CQ are the modifier (DX, POTA, ...).
        i = 0
        modifier: list[str] = []
        while i < len(rest) and not _is_call_or_hash(rest[i]):
            modifier.append(rest[i])
            i += 1
        caller = rest[i] if i < len(rest) else None
        grid = None
        if caller is not None and i + 1 < len(rest) and _is_grid(rest[i + 1]):
            grid = rest[i + 1]
        if caller is None:
            return Message(Kind.REJECT, reject_reason="cq_no_caller", raw=msg)
        return Message(
            Kind.CQ,
            call_de=None if caller.startswith("<") else caller,
            de_hashed=caller.startswith("<"),
            grid=grid,
            cq_modifier=" ".join(modifier) or None,
            raw=msg,
        )

    # --- Standard / non-standard: CALL_TO CALL_DE [EXTRA] -------------------
    if len(tokens) >= 2 and _is_call_or_hash(tokens[0]) and _is_call_or_hash(tokens[1]):
        to_tok, de_tok = tokens[0], tokens[1]
        to_hashed = to_tok.startswith("<")
        de_hashed = de_tok.startswith("<")
        grid = None
        report = None
        if len(tokens) >= 3:
            extra = tokens[2]
            if _is_grid(extra):
                grid = extra
            else:
                report = _report_db(extra)  # None for RRR/RR73/73/unknown
        kind = Kind.NONSTD if (to_hashed or de_hashed) else Kind.STANDARD
        return Message(
            kind,
            call_to=None if to_hashed else to_tok,
            call_de=None if de_hashed else de_tok,
            grid=grid,
            report_db=report,
            to_hashed=to_hashed,
            de_hashed=de_hashed,
            raw=msg,
        )

    return Message(Kind.REJECT, reject_reason="unparseable", raw=msg)
