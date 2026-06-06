"""SoC provider interface."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SocReading:
    """A single state-of-charge reading."""

    percent: float
    # Whether the car reports it is actively charging, if the source knows it.
    # None means "unknown" -- the controller then relies on the charger state.
    charging: bool | None = None


class SocProvider:
    """Reads the battery state of charge of the vehicle."""

    def read(self) -> SocReading:
        """Return the current reading, or raise on failure."""
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources."""
