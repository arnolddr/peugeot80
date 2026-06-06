"""Tests for the AMTRON Compact 2.0s RTU backend, using a fake Modbus client
(no serial port / no wallbox). Verifies heartbeat, release, FLOAT current
encoding, the safety clamp and CP-state decoding."""

from __future__ import annotations

import time

import pytest
from pymodbus.client.mixin import ModbusClientMixin

from peugeot80.charger.base import ChargerState
from peugeot80.charger.mennekes_compact_rtu import (
    HEARTBEAT_VALUE,
    MennekesCompactRtuCharger,
    _decode_cp_state,
)

_FLOAT32 = ModbusClientMixin.DATATYPE.FLOAT32


class _Resp:
    def __init__(self, registers=None) -> None:
        self.registers = registers or []

    def isError(self) -> bool:
        return False


class FakeClient:
    def __init__(self) -> None:
        self.connected = True
        self.writes: list[tuple[int, list[int]]] = []
        self.cp_state = ord("C")

    def connect(self) -> bool:
        return True

    def write_registers(self, address, values, *, device_id=1):
        self.writes.append((address, list(values)))
        return _Resp()

    def read_holding_registers(self, address, *, count=1, device_id=1):
        return _Resp([self.cp_state])

    def read_input_registers(self, address, *, count=1, device_id=1):
        return _Resp([self.cp_state])

    def close(self) -> None:
        self.connected = False


def _charger(client, **over):
    cfg = {"max_current": 16, "min_current": 6, "autostart_heartbeat": False}
    cfg.update(over)
    return MennekesCompactRtuCharger(cfg, client=client)


def _writes_to(client, address):
    return [vals for addr, vals in client.writes if addr == address]


def test_resume_writes_heartbeat_release_and_current():
    c = FakeClient()
    ch = _charger(c)
    ch.resume()
    # heartbeat 0x55AA
    assert _writes_to(c, 0x0D00) == [[HEARTBEAT_VALUE]]
    # release = 1
    assert _writes_to(c, 0x0D05) == [[1]]
    # current setpoint FLOAT32 = 16A, low-word first
    expected = ModbusClientMixin.convert_to_registers(16.0, _FLOAT32, word_order="little")
    assert _writes_to(c, 0x0302) == [expected]


def test_resume_clamps_to_max_current():
    c = FakeClient()
    ch = _charger(c, max_current=10)
    ch.resume()
    expected = ModbusClientMixin.convert_to_registers(10.0, _FLOAT32, word_order="little")
    assert _writes_to(c, 0x0302) == [expected]


def test_pause_releases_and_zeroes_current():
    c = FakeClient()
    ch = _charger(c)
    ch.pause()
    assert _writes_to(c, 0x0D05) == [[0]]
    zero = ModbusClientMixin.convert_to_registers(0.0, _FLOAT32, word_order="little")
    assert _writes_to(c, 0x0302) == [zero]


def test_float_roundtrips():
    regs = ModbusClientMixin.convert_to_registers(16.0, _FLOAT32, word_order="little")
    assert ModbusClientMixin.convert_from_registers(regs, _FLOAT32, word_order="little") == 16.0


def test_heartbeat_value_written():
    c = FakeClient()
    ch = _charger(c)
    ch._send_heartbeat()
    assert _writes_to(c, 0x0D00) == [[HEARTBEAT_VALUE]]


def test_heartbeat_thread_fires_periodically():
    c = FakeClient()
    ch = _charger(c, heartbeat_interval=0.02)
    ch.start_heartbeat()
    time.sleep(0.1)
    ch.close()
    # Several heartbeats should have been written to 0x0D00.
    assert len(_writes_to(c, 0x0D00)) >= 2


def test_rejects_overrated_max_current():
    with pytest.raises(ValueError):
        _charger(FakeClient(), max_current=63)


def test_state_decoding():
    c = FakeClient()
    ch = _charger(c)
    c.cp_state = ord("A")
    assert ch.state() == ChargerState.DISCONNECTED
    c.cp_state = ord("B")
    assert ch.state() == ChargerState.CONNECTED
    c.cp_state = ord("C")
    assert ch.state() == ChargerState.CHARGING


def test_decode_cp_state_ordinals():
    assert _decode_cp_state(1) == ChargerState.DISCONNECTED
    assert _decode_cp_state(2) == ChargerState.CONNECTED
    assert _decode_cp_state(3) == ChargerState.CHARGING
    assert _decode_cp_state(99) == ChargerState.UNKNOWN


def test_close_stops_heartbeat_thread():
    c = FakeClient()
    ch = _charger(c, heartbeat_interval=0.01)
    ch.start_heartbeat()
    time.sleep(0.03)
    ch.close()
    assert ch._hb_thread is not None and not ch._hb_thread.is_alive()
