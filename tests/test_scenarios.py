"""Scenario / failure-mode tests for the controller.

These walk through real-world situations -- sensor dropouts, failed commands,
the wallbox resuming on its own, the car already full when plugged in, the
cloud charger that cannot see an unplug -- and assert the controller stays
fail-safe (never raises current, always retries to stop at the limit).
"""

from __future__ import annotations

import math

from peugeot80.charger.base import ChargerController, ChargerState
from peugeot80.config import DEFAULTS
from peugeot80.controller import Controller
from peugeot80.soc.base import SocProvider, SocReading


# --------------------------------------------------------------------------
# Flexible fakes
# --------------------------------------------------------------------------
class ScriptedSoc(SocProvider):
    """Returns queued readings; an Exception instance is raised when reached."""

    def __init__(self, readings) -> None:
        self._readings = list(readings)
        self._last = self._readings[0] if self._readings else SocReading(0.0)

    def read(self) -> SocReading:
        if self._readings:
            self._last = self._readings.pop(0)
        item = self._last
        if isinstance(item, Exception):
            raise item
        return item


class FakeCharger(ChargerController):
    def __init__(self, state=ChargerState.CHARGING, fail_pause=0, fail_resume=0) -> None:
        self._state = state
        self.fail_pause = fail_pause
        self.fail_resume = fail_resume
        self.calls: list[str] = []

    def state(self) -> ChargerState:
        return self._state

    def set_state(self, state) -> None:
        self._state = state

    def pause(self) -> None:
        self.calls.append("pause")
        if self.fail_pause > 0:
            self.fail_pause -= 1
            raise IOError("simulated pause failure")
        self._state = ChargerState.CONNECTED

    def resume(self) -> None:
        self.calls.append("resume")
        if self.fail_resume > 0:
            self.fail_resume -= 1
            raise IOError("simulated resume failure")
        self._state = ChargerState.CHARGING


def make(soc, charger, **over):
    cfg = dict(DEFAULTS)
    cfg.update(charge_limit=80, hysteresis=2, poll_interval=120, poll_interval_charging=30)
    cfg.update(over)
    return Controller(cfg, soc, charger)


def r(percent, charging=None):
    return SocReading(percent=percent, charging=charging)


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------
def test_already_above_limit_when_plugged_in():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(90, True)]), charger)
    ctl.tick()
    assert charger.calls == ["pause"]
    assert ctl._paused_at_limit


def test_does_nothing_well_below_limit_already_charging():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(40, True)]), charger)
    ctl.tick()
    assert charger.calls == []  # already charging, nothing to do


def test_resumes_idle_session_below_limit():
    charger = FakeCharger(ChargerState.CONNECTED)
    ctl = make(ScriptedSoc([r(40, False)]), charger)
    ctl.tick()
    assert charger.calls == ["resume"]


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------
def test_pause_failure_is_retried_and_not_latched_until_success():
    charger = FakeCharger(ChargerState.CHARGING, fail_pause=2)
    ctl = make(ScriptedSoc([r(85, True), r(85, True), r(85, True)]), charger)

    ctl.tick()
    assert ctl._paused_at_limit is False  # first pause failed
    ctl.tick()
    assert ctl._paused_at_limit is False  # second pause failed
    ctl.tick()
    assert ctl._paused_at_limit is True   # third succeeded
    assert charger.calls == ["pause", "pause", "pause"]


def test_reissues_pause_if_wallbox_resumes_itself():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(85, True), r(85, True)]), charger)
    ctl.tick()
    assert ctl._paused_at_limit
    # The wallbox spontaneously goes back to charging while still above limit.
    charger.set_state(ChargerState.CHARGING)
    ctl.tick()
    assert charger.calls == ["pause", "pause"]  # re-issued the stop


def test_soc_read_failure_holds_state_and_does_not_crash():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(85, True), IOError("cloud down")]), charger)
    ctl.tick()
    assert ctl._paused_at_limit
    sleep = ctl.tick()  # SoC read raises -> must not throw, must not act
    assert sleep == ctl.poll_idle
    assert charger.calls == ["pause"]  # no new command issued
    assert ctl._paused_at_limit  # latch unchanged


def test_implausible_soc_values_are_ignored():
    charger = FakeCharger(ChargerState.CHARGING)
    for bad in (None, -5.0, 150.0, float("nan")):
        charger.calls.clear()
        ctl = make(ScriptedSoc([r(bad, True)]), charger)
        sleep = ctl.tick()
        assert sleep == ctl.poll_idle
        assert charger.calls == []  # never acts on garbage SoC


def test_charger_state_unreadable_still_pauses_at_limit():
    class NoStateCharger(FakeCharger):
        def state(self):
            raise IOError("modbus timeout")

    charger = NoStateCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(85, True)]), charger)
    ctl.tick()
    # state() failed -> UNKNOWN, but we still command pause at the limit.
    assert "pause" in charger.calls
    assert ctl._paused_at_limit


# --------------------------------------------------------------------------
# Latch lifecycle
# --------------------------------------------------------------------------
def test_minor_drift_while_latched_does_not_resume():
    charger = FakeCharger(ChargerState.CHARGING)
    # resume_below=0 -> only a big drop (session_drop) counts as new session.
    ctl = make(ScriptedSoc([r(80, True), r(78, False), r(79, False)]), charger,
               resume_below=0, session_drop=10)
    ctl.tick()  # pause at 80
    assert ctl._paused_at_limit
    ctl.tick()  # drift to 78 -> still latched, no resume
    ctl.tick()  # 79 -> still latched
    assert charger.calls == ["pause"]
    assert ctl._paused_at_limit


def test_big_drop_counts_as_new_session_for_cloud_no_unplug():
    # cloud charger always reports UNKNOWN (cannot see plug state).
    charger = FakeCharger(ChargerState.UNKNOWN)
    ctl = make(ScriptedSoc([r(80, True), r(65, False), r(65, False)]), charger,
               resume_below=0, session_drop=10)
    ctl.tick()  # pause at 80
    assert ctl._paused_at_limit
    ctl.tick()  # dropped to 65 (>10 below limit) -> latch clears
    assert ctl._paused_at_limit is False
    ctl.tick()  # now below limit -> resume charging the new session
    assert "resume" in charger.calls


def test_resume_below_maintain_mode():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(80, True), r(62, False), r(58, False)]), charger,
               resume_below=60)
    ctl.tick()  # pause at 80
    assert ctl._paused_at_limit
    ctl.tick()  # 62 still above resume_below -> stay paused
    assert ctl._paused_at_limit
    ctl.tick()  # 58 <= 60 -> resume
    assert charger.calls[-1] == "resume"
    assert ctl._paused_at_limit is False


def test_unplug_clears_latch_then_new_session_charges():
    charger = FakeCharger(ChargerState.CHARGING)
    ctl = make(ScriptedSoc([r(85, True), r(85, False), r(50, False)]), charger)
    ctl.tick()  # pause at 85
    assert ctl._paused_at_limit
    charger.set_state(ChargerState.DISCONNECTED)
    ctl.tick()  # unplug -> latch clears
    assert ctl._paused_at_limit is False
    charger.set_state(ChargerState.CONNECTED)
    ctl.tick()  # plug in at 50 -> resume
    assert "resume" in charger.calls


# --------------------------------------------------------------------------
# Hysteresis / no flapping
# --------------------------------------------------------------------------
def test_no_flapping_in_hysteresis_band():
    charger = FakeCharger(ChargerState.CONNECTED)
    # Sit at 79 (within [78,80)) repeatedly, not latched: must not toggle.
    ctl = make(ScriptedSoc([r(79, False)] * 5), charger, hysteresis=2)
    for _ in range(5):
        ctl.tick()
    assert charger.calls == []  # no resume/pause churn in the band


def test_invalid_limit_rejected():
    import pytest

    with pytest.raises(ValueError):
        make(ScriptedSoc([r(50)]), FakeCharger(), charge_limit=0)
    with pytest.raises(ValueError):
        make(ScriptedSoc([r(50)]), FakeCharger(), charge_limit=120)
