"""Charger controllers."""

from __future__ import annotations

from .base import ChargerController, ChargerState


def build_charger(cfg: dict) -> ChargerController:
    provider = cfg.get("provider", "mennekes_modbus")
    if provider == "mennekes_modbus":
        from .mennekes_modbus import MennekesModbusCharger

        return MennekesModbusCharger(cfg.get("mennekes_modbus", {}))
    if provider == "cloud_delayed":
        from .cloud_delayed import CloudDelayedCharger

        return CloudDelayedCharger(cfg.get("cloud_delayed", {}))
    raise ValueError(f"unknown charger provider: {provider!r}")


__all__ = ["ChargerController", "ChargerState", "build_charger"]
