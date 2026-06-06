"""Fallback charger control via the Stellantis cloud (psa-car-controller).

For "dumb" wallboxes without Modbus. Pausing puts the car into delayed/deferred
charging via the cloud API; resuming triggers an immediate charge. This depends
on the Stellantis servers and is noticeably less reliable than local control.
"""

from __future__ import annotations

import logging

import requests

from .base import ChargerController, ChargerState

_LOG = logging.getLogger(__name__)


class CloudDelayedCharger(ChargerController):
    def __init__(self, cfg: dict) -> None:
        self.base_url = cfg.get("base_url", "http://127.0.0.1:5000").rstrip("/")
        self.vin = cfg["vin"]
        self.timeout = cfg.get("timeout", 15)

    def _charge_control(self, mode: str) -> None:
        # psa-car-controller: /charge_control?vin=..&hour=..&minute=..
        # "immediate" -> charge now; "delayed" -> defer (effectively stop).
        url = f"{self.base_url}/charge_control"
        params = {"vin": self.vin, "charge_type": mode}
        resp = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        _LOG.info("cloud charge_control -> %s", mode)

    def state(self) -> ChargerState:
        # The cloud cannot reliably tell plugged-vs-charging in real time.
        return ChargerState.UNKNOWN

    def pause(self) -> None:
        self._charge_control("delayed")

    def resume(self) -> None:
        self._charge_control("immediate")
