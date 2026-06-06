"""In-process simulator: a virtual battery + virtual Mennekes wallbox.

Lets you run the real Controller end-to-end without a car or charger. The
virtual battery charges over (optionally accelerated) time while the wallbox
allows current, and stops rising the moment the controller pauses it -- so you
can watch the 80% cutoff happen safely.
"""

from __future__ import annotations

import time
from typing import Callable

from .charger.base import ChargerController, ChargerState
from .soc.base import SocProvider, SocReading


class VirtualBattery:
    """A toy battery model. SoC rises while charging is allowed and plugged in."""

    def __init__(
        self,
        percent: float = 70.0,
        rate_pct_per_sec: float = 1.0,
        connected: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.percent = float(percent)
        self.rate = float(rate_pct_per_sec)
        self.connected = connected
        self.charging_allowed = True
        self._clock = clock
        self._last = clock()

    def _advance(self) -> None:
        now = self._clock()
        dt = now - self._last
        self._last = now
        if dt > 0 and self.connected and self.charging_allowed and self.percent < 100.0:
            self.percent = min(100.0, self.percent + dt * self.rate)

    @property
    def is_charging(self) -> bool:
        self._advance()
        return self.connected and self.charging_allowed and self.percent < 100.0

    def read_percent(self) -> float:
        self._advance()
        return self.percent

    def plug(self) -> None:
        self.connected = True

    def unplug(self) -> None:
        self.connected = False


class SimSoc(SocProvider):
    """SoC provider backed by a VirtualBattery.

    ``fail_after`` lets a test/demo simulate the SoC feed dying after N reads
    (the controller then sees stale data).
    """

    def __init__(self, battery: VirtualBattery, fail_after: int | None = None) -> None:
        self.battery = battery
        self.fail_after = fail_after
        self._reads = 0

    def read(self) -> SocReading:
        self._reads += 1
        if self.fail_after is not None and self._reads > self.fail_after:
            raise IOError("simulated SoC feed down")
        percent = self.battery.read_percent()
        return SocReading(percent=percent, charging=self.battery.is_charging)


class SimCharger(ChargerController):
    """Charger controller backed by a VirtualBattery (mimics a Mennekes)."""

    def __init__(self, battery: VirtualBattery) -> None:
        self.battery = battery

    def state(self) -> ChargerState:
        if not self.battery.connected:
            return ChargerState.DISCONNECTED
        if self.battery.is_charging:
            return ChargerState.CHARGING
        return ChargerState.CONNECTED

    def pause(self) -> None:
        self.battery.charging_allowed = False

    def resume(self) -> None:
        self.battery.charging_allowed = True


class BrokenPauseCharger(SimCharger):
    """A wallbox whose pause() silently does nothing -- mimics a wrong Modbus
    register or a lost connection where the stop command never takes effect."""

    def pause(self) -> None:  # noqa: D401 - intentionally a no-op
        pass
