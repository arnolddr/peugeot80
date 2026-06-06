"""Safety tests for the Mennekes charger: it must never command an unsafe
current, and must refuse an unsafe configuration."""

from __future__ import annotations

import pytest

from peugeot80.charger.mennekes_modbus import ABSOLUTE_MAX_CURRENT, MennekesModbusCharger


def _charger(**over):
    cfg = {"host": "127.0.0.1", "max_current": 16, "min_current": 6}
    cfg.update(over)
    return MennekesModbusCharger(cfg)


def test_rejects_overrated_max_current():
    with pytest.raises(ValueError):
        _charger(max_current=ABSOLUTE_MAX_CURRENT + 1)
    with pytest.raises(ValueError):
        _charger(max_current=0)


def test_clamp_never_exceeds_max_current():
    c = _charger(max_current=16)
    assert c._clamp_current(32) == 16   # request above limit -> clamped down
    assert c._clamp_current(16) == 16
    assert c._clamp_current(10) == 10


def test_clamp_zero_means_pause():
    c = _charger(max_current=16)
    assert c._clamp_current(0) == 0
    assert c._clamp_current(-5) == 0


def test_clamp_raises_to_min_current_when_positive_but_too_low():
    # A positive current below the IEC minimum is raised to min_current, never
    # left at an invalid sub-6A value (0 is the only valid "off").
    c = _charger(max_current=16, min_current=6)
    assert c._clamp_current(3) == 6


def test_unit_kwarg_resolves():
    c = _charger()
    kw = c._unit_kw()
    assert list(kw.keys())[0] in ("device_id", "slave")
