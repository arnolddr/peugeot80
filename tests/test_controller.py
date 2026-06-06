"""Tests for the control-loop logic (no hardware needed)."""

from __future__ import annotations

from peugeot80.charger.base import ChargerController, ChargerState
from peugeot80.config import DEFAULTS
from peugeot80.controller import Controller
from peugeot80.soc.base import SocProvider, SocReading


class FakeSoc(SocProvider):
    def __init__(self, percent: float, charging=None) -> None:
        self.reading = SocReading(percent=percent, charging=charging)

    def read(self) -> SocReading:
        return self.reading


class FakeCharger(ChargerController):
    def __init__(self, state=ChargerState.CHARGING) -> None:
        self._state = state
        self.calls: list[str] = []

    def state(self) -> ChargerState:
        return self._state

    def pause(self) -> None:
        self.calls.append("pause")
        self._state = ChargerState.CONNECTED

    def resume(self) -> None:
        self.calls.append("resume")
        self._state = ChargerState.CHARGING


def _cfg(**over):
    cfg = dict(DEFAULTS)
    cfg.update(over)
    return cfg


def test_pauses_at_limit():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = Controller(_cfg(charge_limit=80), FakeSoc(80.0, charging=True), charger)
    ctl.tick()
    assert charger.calls == ["pause"]
    assert ctl._paused_at_limit is True


def test_does_not_pause_below_limit():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = Controller(_cfg(charge_limit=80), FakeSoc(60.0, charging=True), charger)
    ctl.tick()
    assert charger.calls == []


def test_stays_paused_when_still_above_limit():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = Controller(_cfg(charge_limit=80), FakeSoc(82.0, charging=True), charger)
    ctl.tick()
    ctl.tick()
    assert charger.calls == ["pause"]  # only paused once, no flapping


def test_latch_clears_on_unplug():
    charger = FakeCharger(ChargerState.CHARGING)
    soc = FakeSoc(85.0, charging=True)
    ctl = Controller(_cfg(charge_limit=80), soc, charger)
    ctl.tick()
    assert ctl._paused_at_limit is True
    charger._state = ChargerState.DISCONNECTED
    ctl.tick()
    assert ctl._paused_at_limit is False


def test_resume_below_threshold():
    charger = FakeCharger(ChargerState.CONNECTED)
    soc = FakeSoc(85.0, charging=False)
    ctl = Controller(_cfg(charge_limit=80, resume_below=60), soc, charger)
    ctl.tick()  # pauses at 85
    assert "pause" in charger.calls
    soc.reading = SocReading(percent=55.0, charging=False)
    ctl.tick()  # drops below 60 -> resume
    assert charger.calls[-1] == "resume"
    assert ctl._paused_at_limit is False


def test_idle_poll_interval_when_not_charging():
    charger = FakeCharger(ChargerState.CONNECTED)
    ctl = Controller(_cfg(charge_limit=80, poll_interval=120, poll_interval_charging=30),
                     FakeSoc(50.0, charging=False), charger)
    assert ctl.tick() == 120


def test_charging_poll_interval():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = Controller(_cfg(charge_limit=80, poll_interval=120, poll_interval_charging=30),
                     FakeSoc(50.0, charging=True), charger)
    assert ctl.tick() == 30
