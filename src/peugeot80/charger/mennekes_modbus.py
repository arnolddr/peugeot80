"""MENNEKES AMTRON Xtra/Premium (HCC3) control over Modbus TCP.

Pausing is done by writing the HEMS current limit to 0 A; resuming writes the
configured ``max_current``. Register addresses follow the MENNEKES ECU Modbus
TCP specification but vary with firmware -- confirm them with ``peugeot80 scan``
against your own wallbox before relying on the cutoff.
"""

from __future__ import annotations

import logging

from pymodbus.client import ModbusTcpClient

from .base import ChargerController, ChargerState

_LOG = logging.getLogger(__name__)

# Absolute safety ceiling for a single-phase domestic AC charge point. We refuse
# to be configured above this, and never *write* a current above max_current.
# A HEMS limit can only ever LOWER the wallbox's own configured maximum, so this
# is defence-in-depth on top of the wallbox's hardware limit.
ABSOLUTE_MAX_CURRENT = 32


class MennekesModbusCharger(ChargerController):
    def __init__(self, cfg: dict) -> None:
        self.host = cfg["host"]
        self.port = cfg.get("port", 502)
        self.unit_id = cfg.get("unit_id", 1)
        self.reg_current_limit = cfg.get("reg_hems_current_limit", 1000)
        self.reg_cp_state = cfg.get("reg_cp_state", 122)
        self.min_current = int(cfg.get("min_current", 6))
        self.max_current = int(cfg.get("max_current", 16))

        # Safety validation: max_current must match the installation's rating.
        # Setting it too high could command a current the wiring/breaker cannot
        # carry. We hard-fail rather than risk it.
        if not 0 < self.max_current <= ABSOLUTE_MAX_CURRENT:
            raise ValueError(
                f"max_current={self.max_current}A is out of the safe range "
                f"(1..{ABSOLUTE_MAX_CURRENT}A). Set it to your circuit/wallbox "
                f"rating, never higher."
            )
        if self.min_current < 6:
            _LOG.warning("min_current < 6A is below the IEC 61851 minimum")

        self._client = ModbusTcpClient(self.host, port=self.port)

    def _clamp_current(self, amps: int) -> int:
        """Never command a current above the configured installation limit."""
        if amps <= 0:
            return 0
        clamped = max(self.min_current, min(amps, self.max_current))
        if clamped != amps:
            _LOG.warning("clamped requested %dA to %dA (installation limit)", amps, clamped)
        return clamped

    def _ensure_connected(self) -> None:
        if not self._client.connected:
            if not self._client.connect():
                raise ConnectionError(
                    f"cannot reach Mennekes wallbox at {self.host}:{self.port}"
                )

    def _unit_kw(self) -> dict:
        """pymodbus renamed the slave/unit kwarg to device_id in 3.x."""
        import inspect

        params = inspect.signature(self._client.read_holding_registers).parameters
        key = "device_id" if "device_id" in params else "slave"
        return {key: self.unit_id}

    def _read_register(self, address: int) -> int:
        self._ensure_connected()
        kw = self._unit_kw()
        rr = self._client.read_holding_registers(address, count=1, **kw)
        if rr.isError():
            # Some registers are exposed as input registers; fall back.
            rr = self._client.read_input_registers(address, count=1, **kw)
        if rr.isError():
            raise IOError(f"modbus read error at register {address}: {rr}")
        return rr.registers[0]

    def _write_current_limit(self, amps: int) -> None:
        amps = self._clamp_current(amps)
        self._ensure_connected()
        wr = self._client.write_register(
            self.reg_current_limit, int(amps), **self._unit_kw()
        )
        if wr.isError():
            raise IOError(f"modbus write error at register {self.reg_current_limit}: {wr}")
        _LOG.info("set HEMS current limit to %d A", amps)

    def state(self) -> ChargerState:
        try:
            raw = self._read_register(self.reg_cp_state)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("could not read CP state: %s", exc)
            return ChargerState.UNKNOWN
        return _decode_cp_state(raw)

    def pause(self) -> None:
        self._write_current_limit(0)

    def resume(self) -> None:
        self._write_current_limit(self.max_current)

    def close(self) -> None:
        self._client.close()


# IEC 61851 CP/pilot states. The HCC3 ECU encodes these in a small integer;
# encodings differ between firmware revisions, so we map both the ASCII letter
# convention (A/B/C/D) and small ordinals. Verify with `peugeot80 scan`.
_ASCII_STATE = {
    ord("A"): ChargerState.DISCONNECTED,
    ord("B"): ChargerState.CONNECTED,
    ord("C"): ChargerState.CHARGING,
    ord("D"): ChargerState.CHARGING,
}
_ORDINAL_STATE = {
    0: ChargerState.UNKNOWN,
    1: ChargerState.DISCONNECTED,
    2: ChargerState.CONNECTED,
    3: ChargerState.CHARGING,
    4: ChargerState.CHARGING,
}


def _decode_cp_state(raw: int) -> ChargerState:
    if raw in _ASCII_STATE:
        return _ASCII_STATE[raw]
    return _ORDINAL_STATE.get(raw, ChargerState.UNKNOWN)
