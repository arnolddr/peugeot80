"""End-to-end test of the real Controller against the in-process simulator.

Uses a fake clock so it runs instantly and deterministically -- no sleeps,
no sockets, no hardware.
"""

from __future__ import annotations

from peugeot80.config import DEFAULTS
from peugeot80.controller import Controller
from peugeot80.simulator import SimCharger, SimSoc, VirtualBattery


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _controller(battery, **over):
    cfg = dict(DEFAULTS)
    cfg.update(poll_interval=1, poll_interval_charging=1)
    cfg.update(over)
    return Controller(cfg, SimSoc(battery), SimCharger(battery))


def test_charges_then_stops_at_limit():
    clock = FakeClock()
    battery = VirtualBattery(percent=70.0, rate_pct_per_sec=1.0, clock=clock)
    ctl = _controller(battery, charge_limit=80)

    # Advance 1%/tick until paused. Should pause at ~80%, never reach 100%.
    for _ in range(100):
        ctl.tick()
        if ctl._paused_at_limit:
            break
        clock.advance(1.0)

    assert ctl._paused_at_limit is True
    assert 80.0 <= battery.percent < 82.0  # stopped right at the limit


def test_soc_plateaus_after_pause():
    clock = FakeClock()
    battery = VirtualBattery(percent=79.0, rate_pct_per_sec=1.0, clock=clock)
    ctl = _controller(battery, charge_limit=80)

    for _ in range(10):
        ctl.tick()
        clock.advance(1.0)

    paused_at = battery.percent
    assert ctl._paused_at_limit
    # Keep ticking; SoC must not climb because the wallbox is paused.
    for _ in range(10):
        ctl.tick()
        clock.advance(1.0)
    assert battery.percent == paused_at
    assert battery.percent < 100.0


def test_unplug_resets_latch():
    clock = FakeClock()
    battery = VirtualBattery(percent=85.0, rate_pct_per_sec=1.0, clock=clock)
    ctl = _controller(battery, charge_limit=80)

    ctl.tick()
    assert ctl._paused_at_limit
    battery.unplug()
    ctl.tick()
    assert ctl._paused_at_limit is False


def test_never_exceeds_limit_from_below():
    clock = FakeClock()
    battery = VirtualBattery(percent=50.0, rate_pct_per_sec=2.0, clock=clock)
    ctl = _controller(battery, charge_limit=80)

    peak = 0.0
    for _ in range(100):
        ctl.tick()
        peak = max(peak, battery.percent)
        if ctl._paused_at_limit:
            # a couple more ticks to be sure it doesn't creep up
            for _ in range(3):
                clock.advance(1.0)
                ctl.tick()
            break
        clock.advance(1.0)

    assert ctl._paused_at_limit
    # With 2%/s and 1s ticks we may overshoot by up to one step; ensure it's bounded.
    assert battery.percent <= 82.0
