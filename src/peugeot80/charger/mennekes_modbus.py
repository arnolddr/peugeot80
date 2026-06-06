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


class MennekesModbusCharger(ChargerController):
    def __init__(self, cfg: dict) -> None:
        self.host = cfg["host"]
        self.port = cfg.get("port", 502)
        self.unit_id = cfg.get("unit_id", 1)
        self.reg_current_limit = cfg.get("reg_hems_current_limit", 1000)
        self.reg_cp_state = cfg.get("reg_cp_state", 122)
        self.min_current = cfg.get("min_current", 6)
        self.max_current = cfg.get("max_current", 16)
        self._client = ModbusTcpClient(self.host, port=self.port)

    def _ensure_connected(self) -> None:
        if not self._client.connected:
            if not self._client.connect():
                raise ConnectionError(
                    f"cannot reach Mennekes wallbox at {self.host}:{self.port}"
                )

    def _read_register(self, address: int) -> int:
        self._ensure_connected()
        rr = self._client.read_holding_registers(address, count=1, slave=self.unit_id)
        if rr.isError():
            # Some registers are exposed as input registers; fall back.
            rr = self._client.read_input_registers(address, count=1, slave=self.unit_id)
        if rr.isError():
            raise IOError(f"modbus read error at register {address}: {rr}")
        return rr.registers[0]

    def _write_current_limit(self, amps: int) -> None:
        self._ensure_connected()
        wr = self._client.write_register(
            self.reg_current_limit, int(amps), slave=self.unit_id
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
