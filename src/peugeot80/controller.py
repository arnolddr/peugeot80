"""The control loop: read SoC, pause/resume the charger around the limit.

Design goal: **fail-safe**. If anything is uncertain (SoC unreadable, charger
unreachable, command failed) the controller never raises the charging current
and keeps retrying to *stop* charging when at/above the limit. Its worst-case
failure mode is "charges a bit too far toward 100%", never "delivers unsafe
current".

On top of that it has watchdogs that catch the *functional* (non-safety) ways
the 80% target can be missed: cloud lag (stop margin), a SoC feed that goes
stale, a pause that does not take effect (e.g. wrong Modbus register), and the
app silently dying (heartbeat status file + webhook alerts).
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Callable

from .charger.base import ChargerController, ChargerState
from .notify import Notifier
from .soc.base import SocProvider

_LOG = logging.getLogger(__name__)

# If SoC has fallen this many points below the limit, treat it as a new
# charging session (e.g. the car was driven) even when we cannot detect an
# unplug -- relevant for the cloud charger which has no plug state.
DEFAULT_SESSION_DROP = 10.0


class Controller:
    def __init__(
        self,
        cfg: dict,
        soc: SocProvider,
        charger: ChargerController,
        notifier: Notifier | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.soc = soc
        self.charger = charger
        self.notifier = notifier or Notifier()
        self._clock = clock

        self.limit = float(cfg["charge_limit"])
        self.hysteresis = float(cfg.get("hysteresis", 2))
        self.resume_below = float(cfg.get("resume_below", 0))
        self.session_drop = float(cfg.get("session_drop", DEFAULT_SESSION_DROP))
        self.poll_idle = int(cfg.get("poll_interval", 120))
        self.poll_charging = int(cfg.get("poll_interval_charging", 30))

        # Watchdog / accuracy settings.
        self.stop_margin = float(cfg.get("stop_margin", 0))
        self.soc_max_age = float(cfg.get("soc_max_age", 900))
        self.on_soc_lost = cfg.get("on_soc_lost", "hold")  # hold | pause
        self.pause_tolerance = float(cfg.get("pause_tolerance", 1.5))
        self.status_file = cfg.get("status_file")

        if not 0 < self.limit <= 100:
            raise ValueError(f"charge_limit must be in (0, 100], got {self.limit}")
        if not 0 <= self.stop_margin < self.limit:
            raise ValueError(f"stop_margin must be in [0, {self.limit}), got {self.stop_margin}")
        if self.on_soc_lost not in ("hold", "pause"):
            raise ValueError("on_soc_lost must be 'hold' or 'pause'")

        # Effective threshold we actually stop at (margin compensates lag).
        self._stop_at = self.limit - self.stop_margin

        self._paused_at_limit = False
        self._latched_percent: float | None = None
        self._last_percent: float | None = None
        self._last_ok = clock()
        self._active_alerts: set[str] = set()

    @property
    def _resume_threshold(self) -> float:
        if self.resume_below > 0:
            return self.resume_below
        return max(0.0, self.limit - self.session_drop)

    def tick(self) -> float:
        """Run one evaluation. Returns seconds to sleep before the next tick."""
        now = self._clock()
        percent = self._read_soc()
        charger_state = self._read_state()

        if percent is None:
            return self._handle_lost_soc(now, charger_state)

        self._last_percent = percent
        self._last_ok = now
        self._resolve("soc_stale")

        _LOG.info(
            "SoC=%.1f%% stop_at=%.0f%% limit=%.0f%% charger=%s paused_latch=%s",
            percent, self._stop_at, self.limit, charger_state.value, self._paused_at_limit,
        )

        # New session: physically unplugged, or SoC dropped (car was driven).
        if charger_state == ChargerState.DISCONNECTED:
            self._clear_latch("vehicle unplugged")
            self._write_status(now, percent, charger_state)
            return self.poll_idle
        if self._paused_at_limit and percent <= self._resume_threshold:
            self._clear_latch(f"SoC dropped to {percent:.1f}%")

        # Watchdog: did a pause actually take effect? If SoC keeps climbing
        # while we believe we're paused, the stop did not work (e.g. wrong
        # Modbus register). Alert -- and keep re-issuing the pause below.
        if self._paused_at_limit and self._latched_percent is not None:
            if percent > self._latched_percent + self.pause_tolerance:
                self._alert(
                    "pause_ineffective",
                    f"SoC steeg naar {percent:.1f}% NA pauze bij "
                    f"{self._latched_percent:.1f}% -- pauze lijkt niet te werken "
                    f"(controleer reg_hems_current_limit / Modbus-verbinding!)",
                )

        if percent >= self._stop_at:
            # At/above the stop point: ensure charging is actually stopped.
            if not self._paused_at_limit or charger_state == ChargerState.CHARGING:
                _LOG.info("at/above stop point (%.1f%% >= %.0f%%) -- pausing",
                          percent, self._stop_at)
                if self._safe(self.charger.pause):
                    self._paused_at_limit = True
                    if self._latched_percent is None:
                        self._latched_percent = percent
                else:
                    _LOG.error("pause command failed -- will retry next tick")
        elif not self._paused_at_limit and percent < self._stop_at - self.hysteresis:
            # Comfortably below the limit and not latched: charging is allowed.
            if charger_state in (ChargerState.CONNECTED, ChargerState.UNKNOWN):
                self._safe(self.charger.resume)
        # else: within the hysteresis band, or latched -> hold, do nothing.

        is_active = (not self._paused_at_limit) and charger_state == ChargerState.CHARGING
        self._write_status(now, percent, charger_state)
        return self.poll_charging if is_active else self.poll_idle

    # -- watchdogs / helpers --------------------------------------------------
    def _handle_lost_soc(self, now: float, charger_state: ChargerState) -> float:
        age = now - self._last_ok
        if self.soc_max_age > 0 and age > self.soc_max_age and not self._paused_at_limit:
            self._alert(
                "soc_stale",
                f"SoC al {int(age)}s niet leesbaar/bijgewerkt terwijl mogelijk "
                f"wordt geladen (max {int(self.soc_max_age)}s).",
            )
            if self.on_soc_lost == "pause":
                _LOG.warning("on_soc_lost=pause -- preventively pausing charge")
                if self._safe(self.charger.pause):
                    self._paused_at_limit = True
        else:
            _LOG.warning("SoC unavailable (age %ds) -- holding (latch=%s)",
                         int(age), self._paused_at_limit)
        self._write_status(now, None, charger_state)
        return self.poll_idle

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
        self._latched_percent = None
        self._resolve("pause_ineffective")

    def _alert(self, key: str, message: str) -> None:
        if key in self._active_alerts:
            return
        self._active_alerts.add(key)
        self.notifier.alert(message)

    def _resolve(self, key: str) -> None:
        if key in self._active_alerts:
            self._active_alerts.discard(key)
            _LOG.info("alert resolved: %s", key)

    def _write_status(self, now: float, percent: float | None, state: ChargerState) -> None:
        if not self.status_file:
            return
        payload = {
            "timestamp": time.time(),
            "soc_percent": percent,
            "charger_state": state.value,
            "paused_at_limit": self._paused_at_limit,
            "limit": self.limit,
            "stop_at": self._stop_at,
            "soc_age_seconds": round(now - self._last_ok, 1),
            "active_alerts": sorted(self._active_alerts),
        }
        try:
            Path(self.status_file).write_text(json.dumps(payload, indent=2))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("could not write status file: %s", exc)

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
        _LOG.info("peugeot80 controller started (limit=%.0f%%, stop_at=%.0f%%)",
                  self.limit, self._stop_at)
        try:
            while True:
                sleep_for = self.tick()
                time.sleep(max(5, sleep_for))
        except KeyboardInterrupt:
            _LOG.info("stopping")
        finally:
            self.soc.close()
            self.charger.close()
