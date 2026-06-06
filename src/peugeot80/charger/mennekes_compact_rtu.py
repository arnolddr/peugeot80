"""MENNEKES AMTRON Compact 2.0s / Start 2.0s control over Modbus RTU.

Unlike the Xtra/Premium (HCC3, Modbus TCP), the Compact 2.0s is a HEMS *slave*
on an RS-485 bus. An energy manager must:

  * keep a **heartbeat** alive: write 0x55AA to reg 0x0D00 at least every ~8s,
  * set the **charging release** (reg 0x0D05) to 1 to allow charging,
  * set the **current setpoint** (reg 0x0302, FLOAT32 amps, >= 6A) to charge.

If the heartbeat stops, the wallbox stops charging -- so this design is
inherently fail-safe: if this app or its host dies, charging halts within
~10s. The flip side: the wallbox only charges while this app is running and
permitting it.

Transport is either a direct serial RS-485 adapter (``transport: serial``) or a
RS485<->TCP gateway such as an Elfin EW11 (``transport: tcp``).

Register addresses follow the MENNEKES AMTRON Compact 2.0s Modbus RTU
specification (v1.2) but are all configurable -- confirm them with
``peugeot80 scan`` against your unit before relying on the cutoff.
"""

from __future__ import annotations

import logging
import threading

from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from pymodbus.client.mixin import ModbusClientMixin

from .base import ChargerController, ChargerState
from .mennekes_modbus import ABSOLUTE_MAX_CURRENT

_LOG = logging.getLogger(__name__)

_FLOAT32 = ModbusClientMixin.DATATYPE.FLOAT32
HEARTBEAT_VALUE = 0x55AA


class MennekesCompactRtuCharger(ChargerController):
    def __init__(self, cfg: dict, client=None) -> None:
        self.unit_id = cfg.get("unit_id", 1)
        self.min_current = int(cfg.get("min_current", 6))
        self.max_current = int(cfg.get("max_current", 16))
        self.word_order = cfg.get("word_order", "little")  # Mennekes: low word first
        self.heartbeat_interval = float(cfg.get("heartbeat_interval", 8))

        # Register map (MENNEKES AMTRON Compact 2.0s RTU spec v1.2). Verify!
        self.reg_heartbeat = cfg.get("reg_heartbeat", 0x0D00)
        self.reg_charge_release = cfg.get("reg_charge_release", 0x0D05)
        self.reg_current_setpoint = cfg.get("reg_current_setpoint", 0x0302)
        self.reg_cp_state = cfg.get("reg_cp_state", 0x0100)

        if not 0 < self.max_current <= ABSOLUTE_MAX_CURRENT:
            raise ValueError(
                f"max_current={self.max_current}A out of safe range "
                f"(1..{ABSOLUTE_MAX_CURRENT}A)."
            )

        if client is not None:
            self._client = client
        elif cfg.get("transport", "serial") == "tcp":
            # RS485<->TCP gateway (e.g. Elfin EW11)
            self._client = ModbusTcpClient(
                cfg["host"], port=cfg.get("port", 502), timeout=cfg.get("timeout", 3)
            )
        else:
            self._client = ModbusSerialClient(
                port=cfg["port"],
                baudrate=cfg.get("baudrate", 57600),
                bytesize=8,
                parity=cfg.get("parity", "N"),
                stopbits=cfg.get("stopbits", 2),
                timeout=cfg.get("timeout", 2),
            )

        self._lock = threading.Lock()
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None
        if cfg.get("autostart_heartbeat", True):
            self.start_heartbeat()

    # -- safety clamp (shared semantics with the TCP charger) -----------------
    def _clamp_current(self, amps: int) -> int:
        if amps <= 0:
            return 0
        clamped = max(self.min_current, min(amps, self.max_current))
        if clamped != amps:
            _LOG.warning("clamped requested %dA to %dA (installation limit)", amps, clamped)
        return clamped

    # -- modbus helpers -------------------------------------------------------
    def _unit_kw(self) -> dict:
        import inspect

        params = inspect.signature(self._client.write_registers).parameters
        key = "device_id" if "device_id" in params else "slave"
        return {key: self.unit_id}

    def _ensure_connected(self) -> None:
        if not self._client.connected:
            if not self._client.connect():
                raise ConnectionError("cannot reach Mennekes Compact (RS-485/gateway)")

    def _write_uint16(self, address: int, value: int) -> None:
        self._ensure_connected()
        wr = self._client.write_registers(address, [int(value)], **self._unit_kw())
        if wr.isError():
            raise IOError(f"modbus write error at {hex(address)}: {wr}")

    def _write_float(self, address: int, value: float) -> None:
        self._ensure_connected()
        regs = ModbusClientMixin.convert_to_registers(
            float(value), _FLOAT32, word_order=self.word_order
        )
        wr = self._client.write_registers(address, regs, **self._unit_kw())
        if wr.isError():
            raise IOError(f"modbus write error at {hex(address)}: {wr}")

    def _read_uint16(self, address: int) -> int:
        self._ensure_connected()
        kw = self._unit_kw()
        rr = self._client.read_holding_registers(address, count=1, **kw)
        if rr.isError():
            rr = self._client.read_input_registers(address, count=1, **kw)
        if rr.isError():
            raise IOError(f"modbus read error at {hex(address)}: {rr}")
        return rr.registers[0]

    # -- heartbeat ------------------------------------------------------------
    def start_heartbeat(self) -> None:
        if self._hb_thread and self._hb_thread.is_alive():
            return
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="mennekes-heartbeat", daemon=True
        )
        self._hb_thread.start()

    def _send_heartbeat(self) -> None:
        with self._lock:
            self._write_uint16(self.reg_heartbeat, HEARTBEAT_VALUE)

    def _heartbeat_loop(self) -> None:
        # Send one immediately, then every heartbeat_interval until stopped.
        while True:
            try:
                self._send_heartbeat()
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                _LOG.warning("heartbeat write failed: %s", exc)
            if self._hb_stop.wait(self.heartbeat_interval):
                return

    # -- ChargerController ----------------------------------------------------
    def state(self) -> ChargerState:
        try:
            with self._lock:
                raw = self._read_uint16(self.reg_cp_state)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("could not read CP state: %s", exc)
            return ChargerState.UNKNOWN
        return _decode_cp_state(raw)

    def pause(self) -> None:
        # Stop charging but keep the heartbeat alive so the wallbox stays in a
        # controlled idle state (not an error state).
        with self._lock:
            self._write_uint16(self.reg_charge_release, 0)
            self._write_float(self.reg_current_setpoint, 0.0)
        _LOG.info("charging released=0 (paused)")

    def resume(self) -> None:
        amps = self._clamp_current(self.max_current)
        # (Re)write heartbeat + release + setpoint -- this also recovers the
        # wallbox from an error state caused by an earlier heartbeat loss.
        with self._lock:
            self._write_uint16(self.reg_heartbeat, HEARTBEAT_VALUE)
            self._write_uint16(self.reg_charge_release, 1)
            self._write_float(self.reg_current_setpoint, float(amps))
        _LOG.info("charging released=1 at %dA (resumed)", amps)

    def close(self) -> None:
        self._hb_stop.set()
        if self._hb_thread:
            self._hb_thread.join(timeout=2)
        try:
            with self._lock:
                self._write_uint16(self.reg_charge_release, 0)
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass
        self._client.close()


# IEC 61851 CP states. Exact encoding of the Compact 2.0s status register must
# be confirmed with `peugeot80 scan`; we map both the ASCII letters and small
# ordinals seen in the field.
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
