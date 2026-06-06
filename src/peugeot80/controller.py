"""The control loop: read SoC, pause/resume the charger around the limit."""

from __future__ import annotations

import logging
import time

from .charger.base import ChargerController, ChargerState
from .soc.base import SocProvider

_LOG = logging.getLogger(__name__)


class Controller:
    def __init__(self, cfg: dict, soc: SocProvider, charger: ChargerController) -> None:
        self.cfg = cfg
        self.soc = soc
        self.charger = charger

        self.limit = float(cfg["charge_limit"])
        self.hysteresis = float(cfg.get("hysteresis", 2))
        self.resume_below = float(cfg.get("resume_below", 0))
        self.poll_idle = int(cfg.get("poll_interval", 120))
        self.poll_charging = int(cfg.get("poll_interval_charging", 30))

        # Latch: once we pause at the limit we stay paused until the car is
        # unplugged (or, if resume_below > 0, until SoC drops below it).
        self._paused_at_limit = False

    def tick(self) -> float:
        """Run one evaluation. Returns seconds to sleep before the next tick."""
        try:
            reading = self.soc.read()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("SoC read failed: %s", exc)
            return self.poll_idle

        try:
            charger_state = self.charger.state()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("charger state read failed: %s", exc)
            charger_state = ChargerState.UNKNOWN

        percent = reading.percent
        _LOG.info(
            "SoC=%.1f%% limit=%.0f%% charger=%s paused_latch=%s",
            percent, self.limit, charger_state.value, self._paused_at_limit,
        )

        # Reset the latch when the car is unplugged so the next session works.
        if charger_state == ChargerState.DISCONNECTED:
            if self._paused_at_limit:
                _LOG.info("vehicle unplugged -- clearing pause latch")
            self._paused_at_limit = False
            return self.poll_idle

        if percent >= self.limit and not self._paused_at_limit:
            _LOG.info("reached limit (%.1f%% >= %.0f%%) -- pausing charge", percent, self.limit)
            self._safe(self.charger.pause)
            self._paused_at_limit = True
        elif self._paused_at_limit and self._should_resume(percent):
            _LOG.info("SoC dropped to %.1f%% -- resuming charge", percent)
            self._safe(self.charger.resume)
            self._paused_at_limit = False
        elif not self._paused_at_limit and percent < self.limit - self.hysteresis:
            # Below the limit and not latched: make sure charging is allowed.
            if charger_state == ChargerState.CONNECTED:
                self._safe(self.charger.resume)

        charging = reading.charging
        is_active = charging is True or charger_state == ChargerState.CHARGING
        return self.poll_charging if is_active else self.poll_idle

    def _should_resume(self, percent: float) -> bool:
        if self.resume_below <= 0:
            return False
        return percent <= self.resume_below

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            _LOG.error("charger command failed: %s", exc)

    def run(self) -> None:
        _LOG.info("peugeot80 controller started (limit=%.0f%%)", self.limit)
        try:
            while True:
                sleep_for = self.tick()
                time.sleep(max(5, sleep_for))
        except KeyboardInterrupt:
            _LOG.info("stopping")
        finally:
            self.soc.close()
            self.charger.close()
