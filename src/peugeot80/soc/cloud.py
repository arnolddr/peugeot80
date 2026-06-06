"""SoC via a running psa-car-controller instance (Stellantis cloud).

See https://github.com/flobz/psa_car_controller -- it exposes
``GET /get_vehicleinfo/<VIN>`` returning JSON with the battery level.
"""

from __future__ import annotations

import logging

import requests

from .base import SocProvider, SocReading

_LOG = logging.getLogger(__name__)


class CloudSocProvider(SocProvider):
    def __init__(self, cfg: dict) -> None:
        self.base_url = cfg.get("base_url", "http://127.0.0.1:5000").rstrip("/")
        self.vin = cfg["vin"]
        self.timeout = cfg.get("timeout", 15)

    def read(self) -> SocReading:
        url = f"{self.base_url}/get_vehicleinfo/{self.vin}"
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        percent = _extract_battery_level(data)
        if percent is None:
            raise ValueError(f"could not find battery level in response from {url}")
        charging = _extract_charging(data)
        _LOG.debug("cloud SoC=%.1f%% charging=%s", percent, charging)
        return SocReading(percent=percent, charging=charging)


def _extract_battery_level(data: dict) -> float | None:
    """psa-car-controller nests this under energy[].level; be lenient."""
    energy = data.get("energy")
    if isinstance(energy, list):
        for entry in energy:
            if isinstance(entry, dict) and entry.get("level") is not None:
                return float(entry["level"])
    if isinstance(energy, dict) and energy.get("level") is not None:
        return float(energy["level"])
    for key in ("level", "battery_level", "soc"):
        if data.get(key) is not None:
            return float(data[key])
    return None


def _extract_charging(data: dict) -> bool | None:
    energy = data.get("energy")
    entries = energy if isinstance(energy, list) else [energy] if isinstance(energy, dict) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        charging = entry.get("charging")
        if isinstance(charging, dict):
            status = charging.get("status") or charging.get("charging_status")
            if status is not None:
                return str(status).lower() in ("in_progress", "charging", "inprogress")
        if isinstance(charging, bool):
            return charging
    return None
