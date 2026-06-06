"""Charger controller interface."""

from __future__ import annotations

import enum


class ChargerState(enum.Enum):
    """Coarse charger state the controller cares about."""

    DISCONNECTED = "disconnected"  # no vehicle plugged in
    CONNECTED = "connected"        # plugged in, not drawing current
    CHARGING = "charging"          # actively delivering current
    UNKNOWN = "unknown"


class ChargerController:
    """Controls whether the wallbox delivers current."""

    def state(self) -> ChargerState:
        raise NotImplementedError

    def pause(self) -> None:
        """Stop delivering current (vehicle stays plugged in)."""
        raise NotImplementedError

    def resume(self) -> None:
        """Allow current to flow again."""
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources."""
