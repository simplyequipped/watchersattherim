"""Tests for propagation metric math (per-mode quality normalization)."""

from watchersattherim.propagation.metrics import quality


def test_quality_ft8_default_scale():
    assert quality(-24) == 0.0          # FT8 floor
    assert quality(10) == 1.0           # shared ceiling
    assert quality(-7) == 0.5           # midpoint of [-24, 10]


def test_quality_clamps():
    assert quality(-99) == 0.0
    assert quality(99) == 1.0


def test_quality_wspr_floor_is_deeper():
    assert quality(-28, "WSPR") == 0.0          # WSPR floor
    # a -24 signal is at the FT8 floor (0.0) but above the WSPR floor (>0)
    assert quality(-24, "FT8") == 0.0
    assert quality(-24, "WSPR") > 0.0


def test_quality_unknown_mode_falls_back_to_ft8():
    assert quality(-24, "JT65") == quality(-24, "FT8")
