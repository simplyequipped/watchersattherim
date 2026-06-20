"""Shared metric math for the propagation layer.

quality is an absolute, fixed-scale normalization of median SNR (comparable
across bands/times/maps). confidence is a 0..1 trust signal whose inputs vary by
endpoint; the factors are composed here, the formula chosen by each caller.
"""

from __future__ import annotations

# quality: median SNR mapped onto a fixed scale, clamped. The floor tracks each
# mode's practical decode sensitivity (the ceiling is shared). WSPR decodes
# deeper than FT8; we use -28 (our conservative wsprd has no OSD/-w, so it does
# not reach the deeper, false-prone -30 tier).
SNR_FLOOR, SNR_CEIL = -24.0, 10.0      # FT8 default scale
_MODE_FLOOR = {"FT8": -24.0, "WSPR": -28.0}

# confidence factors.
CONF_FULL_COUNT = 10          # observations at which the volume factor saturates
FRESH_FULL_SEC = 1800         # age at which the freshness factor reaches 0 (nowcast)
WIDEN_PENALTY = 0.7           # per level the grid match widened beyond requested


def quality(median_snr: float, mode: str = "FT8") -> float:
    floor = _MODE_FLOOR.get(mode, SNR_FLOOR)
    span = SNR_CEIL - floor
    return round(min(1.0, max(0.0, (median_snr - floor) / span)), 2)


def volume_factor(observations: int) -> float:
    return min(1.0, observations / CONF_FULL_COUNT)


def freshness_factor(newest_age_sec: int) -> float:
    return max(0.0, 1.0 - newest_age_sec / FRESH_FULL_SEC)


def widen_factor(levels: int) -> float:
    return WIDEN_PENALTY ** max(0, levels)


def channel_confidence(observations: int, newest_age_sec: int, widen_levels: int) -> float:
    """Nowcast trust: volume x freshness x widening penalty."""
    vol = volume_factor(observations)
    fresh = freshness_factor(newest_age_sec)
    return round(vol * (0.3 + 0.7 * fresh) * widen_factor(widen_levels), 2)


def historical_confidence(observations: int) -> float:
    """Historical trust: volume only (old data does not go stale; no widening)."""
    return round(volume_factor(observations), 2)
