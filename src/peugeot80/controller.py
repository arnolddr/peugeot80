"""The control loop: read SoC, pause/resume the charger around the limit.

Design goal: **fail-safe**. If anything is uncertain (SoC unreadable, charger
unreachable, command failed) the controller never raises the charging current
and keeps retrying to *stop* charging when at/above the limit. Its worst-case
failure mode is "charges a bit too far toward 100%", never "delivers unsafe
current".
"""

from __future__ import annotations

import logging
import math
import time

from .charger.base import ChargerController, ChargerState
from .soc.base import SocProvider

_LOG = logging.getLogger(__name__)

# If SoC has fallen this many points below the limit, treat it as a new
# charging session (e.g. the car was driven) even when we cannot detect an
# unplug -- relevant for the cloud charger which has no plug state.
DEFAULT_SESSION_DROP = 10.0


class Controller:
    def __init__(self, cfg: dict, soc: SocProvider, charger: ChargerController) -> None:
        self.cfg = cfg
        self.soc = soc
        self.charger = charger

        self.limit = float(cfg["charge_limit"])
        self.hysteresis = float(cfg.get("hysteresis", 2))
        self.resume_below = float(cfg.get("resume_below", 0))
        self.session_drop = float(cfg.get("session_drop", DEFAULT_SESSION_DROP))
        self.poll_idle = int(cfg.get("poll_interval", 120))
        self.poll_charging = int(cfg.get("poll_interval_charging", 30))

        if not 0 < self.limit <= 100:
            raise ValueError(f"charge_limit must be in (0, 100], got {self.limit}")

        # Latch: we reached the limit and charging should stay off until either
        # the car is unplugged or the SoC drops enough to count as a new session.
        self._paused_at_limit = False

    # -- threshold below which we consider charging "allowed again" -----------
    @property
    def _resume_threshold(self) -> float:
        if self.resume_below > 0:
            return self.resume_below
        return max(0.0, self.limit - self.session_drop)

    def tick(self) -> float:
        """Run one evaluation. Returns seconds to sleep before the next tick."""
        percent = self._read_soc()
        charger_state = self._read_state()

        if percent is None:
            # Unknown SoC: do nothing that could be unsafe. Hold current state.
            _LOG.warning("SoC unavailable -- holding (latch=%s)", self._paused_at_limit)
            return self.poll_idle

        _LOG.info(
            "SoC=%.1f%% limit=%.0f%% charger=%s paused_latch=%s",
            percent, self.limit, charger_state.value, self._paused_at_limit,
        )

        # New session: physically unplugged, or SoC dropped (car was driven).
        if charger_state == ChargerState.DISCONNECTED:
            self._clear_latch("vehicle unplugged")
            return self.poll_idle
        if self._paused_at_limit and percent <= self._resume_threshold:
            self._clear_latch(f"SoC dropped to {percent:.1f}%")

        if percent >= self.limit:
            # At/above limit: ensure charging is actually stopped. Re-issue the
            # pause if not yet latched OR if the charger still reports charging
            # (e.g. the wallbox resumed on its own, or a prior pause didn't take).
            if not self._paused_at_limit or charger_state == ChargerState.CHARGING:
                _LOG.info("at/above limit (%.1f%% >= %.0f%%) -- pausing", percent, self.limit)
                if self._safe(self.charger.pause):
                    self._paused_at_limit = True
                else:
                    _LOG.error("pause command failed -- will retry next tick")
        elif not self._paused_at_limit and percent < self.limit - self.hysteresis:
            # Comfortably below the limit and not latched: charging is allowed.
            if charger_state in (ChargerState.CONNECTED, ChargerState.UNKNOWN):
                self._safe(self.charger.resume)
        # else: within the hysteresis band, or latched -> hold, do nothing.

        is_active = reading_is_active(percent, charger_state, self._paused_at_limit)
        return self.poll_charging if is_active else self.poll_idle

    # -- helpers --------------------------------------------------------------
    def _read_soc(self) -> float | None:
        try:
            reading = self.soc.read()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("SoC read failed: %s", exc)
            return None
        percent = reading.percent
        if percent is None or math.isnan(percent) or not 0 <= percent <= 100:
            _LOG.warning("implausible SoC reading %r -- ignoring", percent)
            return None
        return float(percent)

    def _read_state(self) -> ChargerState:
        try:
            return self.charger.state()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("charger state read failed: %s", exc)
            return ChargerState.UNKNOWN

    def _clear_latch(self, reason: str) -> None:
        if self._paused_at_limit:
            _LOG.info("clearing pause latch (%s)", reason)
        self._paused_at_limit = False

    @staticmethod
    def _safe(fn) -> bool:
        """Run a charger command. Returns True on success, False on failure."""
        try:
            fn()
            return True
        except Exception as exc:  # noqa: BLE001
            _LOG.error("charger command failed: %s", exc)
            return False

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


def reading_is_active(percent: float, state: ChargerState, latched: bool) -> bool:
    """Whether to poll at the faster 'charging' cadence."""
    if latched:
        return False
    return state == ChargerState.CHARGING
