"""Tests for the accuracy/watchdog layer: stop margin, stale-SoC watchdog,
pause-effectiveness check, status heartbeat, and alert de-duplication."""

from __future__ import annotations

import json

from peugeot80.charger.base import ChargerState
from peugeot80.config import DEFAULTS
from peugeot80.controller import Controller
from peugeot80.soc.base import SocReading

from test_scenarios import FakeCharger, ScriptedSoc, r  # reuse fakes


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt) -> None:
        self.t += dt


class CapturingNotifier:
    def __init__(self) -> None:
        self.alerts: list[str] = []

    def alert(self, message: str) -> None:
        self.alerts.append(message)

    def info(self, message: str) -> None:
        pass


def make(soc, charger, clock=None, notifier=None, **over):
    cfg = dict(DEFAULTS)
    cfg.update(charge_limit=80, hysteresis=2, poll_interval=120, poll_interval_charging=30)
    cfg.update(over)
    return Controller(cfg, soc, charger, notifier=notifier, clock=clock or FakeClock())


# --------------------------------------------------------------------------
# Stop margin (cloud lag compensation)
# --------------------------------------------------------------------------
def test_stop_margin_stops_early():
    charger = FakeCharger(ChargerState.CHARGING)
    # margin 2 -> effective stop at 78
    ctl = make(ScriptedSoc([r(78, True)]), charger, stop_margin=2)
    ctl.tick()
    assert ctl._paused_at_limit
    assert charger.calls == ["pause"]


def test_stop_margin_does_not_stop_before_threshold():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(77, True)]), charger, stop_margin=2)
    ctl.tick()
    assert not ctl._paused_at_limit


def test_invalid_stop_margin_rejected():
    import pytest

    with pytest.raises(ValueError):
        make(ScriptedSoc([r(50)]), FakeCharger(), stop_margin=80)


# --------------------------------------------------------------------------
# Stale-SoC watchdog
# --------------------------------------------------------------------------
def test_stale_soc_alerts_after_max_age():
    clock = FakeClock()
    notifier = CapturingNotifier()
    # first reading ok, then the feed dies (raises)
    soc = ScriptedSoc([r(60, True), IOError("cloud down")])
    ctl = make(soc, FakeCharger(ChargerState.CHARGING), clock=clock,
               notifier=notifier, soc_max_age=300, on_soc_lost="hold")
    ctl.tick()                 # ok read, resets last_ok
    clock.advance(301)         # exceed max age
    ctl.tick()                 # read fails -> stale alert
    assert any("niet leesbaar" in a for a in notifier.alerts)


def test_stale_soc_does_not_alert_within_max_age():
    clock = FakeClock()
    notifier = CapturingNotifier()
    soc = ScriptedSoc([r(60, True), IOError("blip")])
    ctl = make(soc, FakeCharger(ChargerState.CHARGING), clock=clock,
               notifier=notifier, soc_max_age=300)
    ctl.tick()
    clock.advance(60)          # short blip
    ctl.tick()
    assert notifier.alerts == []


def test_on_soc_lost_pause_preventively_stops():
    clock = FakeClock()
    charger = FakeCharger(ChargerState.CHARGING)
    soc = ScriptedSoc([r(60, True), IOError("down"), IOError("down")])
    ctl = make(soc, charger, clock=clock, notifier=CapturingNotifier(),
               soc_max_age=300, on_soc_lost="pause")
    ctl.tick()
    clock.advance(301)
    ctl.tick()                 # stale -> preventive pause
    assert "pause" in charger.calls
    assert ctl._paused_at_limit


def test_stale_alert_clears_when_soc_returns():
    clock = FakeClock()
    notifier = CapturingNotifier()
    soc = ScriptedSoc([r(60, True), IOError("down"), r(61, True)])
    ctl = make(soc, FakeCharger(ChargerState.CHARGING), clock=clock,
               notifier=notifier, soc_max_age=300)
    ctl.tick()
    clock.advance(301)
    ctl.tick()                 # stale alert raised
    assert "soc_stale" in ctl._active_alerts
    ctl.tick()                 # soc back -> resolved
    assert "soc_stale" not in ctl._active_alerts


# --------------------------------------------------------------------------
# Pause-effectiveness watchdog (catches wrong register)
# --------------------------------------------------------------------------
def test_pause_ineffective_alerts_when_soc_keeps_rising():
    notifier = CapturingNotifier()

    # Charger whose pause() does nothing (mimics wrong register: state stays
    # CHARGING and SoC keeps climbing).
    class NoOpPauseCharger(FakeCharger):
        def pause(self):
            self.calls.append("pause")  # pretends to work but doesn't stop

    charger = NoOpPauseCharger(ChargerState.CHARGING)
    soc = ScriptedSoc([r(80, True), r(83, True)])
    ctl = make(soc, charger, notifier=notifier, pause_tolerance=1.5)
    ctl.tick()                 # latch at 80
    assert ctl._paused_at_limit
    ctl.tick()                 # SoC rose to 83 -> ineffective alert
    assert any("pauze lijkt niet te werken" in a for a in notifier.alerts)


def test_alerts_are_deduplicated():
    notifier = CapturingNotifier()

    class NoOpPauseCharger(FakeCharger):
        def pause(self):
            self.calls.append("pause")

    charger = NoOpPauseCharger(ChargerState.CHARGING)
    soc = ScriptedSoc([r(80, True), r(83, True), r(84, True)])
    ctl = make(soc, charger, notifier=notifier, pause_tolerance=1.5)
    ctl.tick()
    ctl.tick()
    ctl.tick()
    # Only one pause_ineffective alert despite repeated rises.
    assert sum("pauze lijkt niet te werken" in a for a in notifier.alerts) == 1


# --------------------------------------------------------------------------
# Heartbeat status file
# --------------------------------------------------------------------------
def test_status_file_is_written(tmp_path):
    path = tmp_path / "status.json"
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(85, True)]), charger, status_file=str(path))
    ctl.tick()
    data = json.loads(path.read_text())
    assert data["soc_percent"] == 85
    assert data["paused_at_limit"] is True
    assert data["limit"] == 80
    assert "soc_age_seconds" in data
