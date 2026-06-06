"""Probe a Mennekes wallbox over Modbus to identify model and registers.

Supports Modbus TCP (AMTRON Xtra/Premium, or a Compact 2.0s behind an
RS485<->TCP gateway) and direct serial RTU (AMTRON Compact/Start 2.0s).
"""

from __future__ import annotations

from pymodbus.client import ModbusSerialClient, ModbusTcpClient


def _unit_kw(client, unit_id: int) -> dict:
    import inspect

    params = inspect.signature(client.read_holding_registers).parameters
    key = "device_id" if "device_id" in params else "slave"
    return {key: unit_id}


def _dump(client, unit_id: int, start: int, count: int) -> None:
    kw = _unit_kw(client, unit_id)
    for label, reader in (
        ("HOLDING", client.read_holding_registers),
        ("INPUT", client.read_input_registers),
    ):
        print(f"== {label} registers {start}..{start + count - 1} ==")
        addr = start
        while addr < start + count:
            chunk = min(8, start + count - addr)
            rr = reader(addr, count=chunk, **kw)
            if rr.isError():
                addr += chunk
                continue
            for i, value in enumerate(rr.registers):
                a = addr + i
                ascii_hint = chr(value) if 32 <= value < 127 else ""
                print(f"  [{a:5d}] 0x{a:04X}  {value:6d}  0x{value:04X}  {ascii_hint}")
            addr += chunk
        print()


def scan(host: str, port: int = 502, unit_id: int = 1, start: int = 0, count: int = 200) -> int:
    """Probe over Modbus TCP. Returns 0 if reachable, 1 if not."""
    client = ModbusTcpClient(host, port=port, timeout=5)
    print(f"Connecting (TCP) to {host}:{port} (unit {unit_id}) ...")
    if not client.connect():
        print("  -> NO CONNECTION. Either the IP/port is wrong, Modbus TCP is off,")
        print("     or this is a Compact/Start 2.0s (RS-485 only -> use `scan-rtu`).")
        return 1
    print("  -> connected. Dumping registers.\n")
    _dump(client, unit_id, start, count)
    client.close()
    _tips()
    return 0


def scan_rtu(port: str, baudrate: int = 57600, parity: str = "N", stopbits: int = 2,
             unit_id: int = 1, start: int = 0, count: int = 256) -> int:
    """Probe an AMTRON Compact/Start 2.0s over serial RS-485 (Modbus RTU)."""
    client = ModbusSerialClient(
        port=port, baudrate=baudrate, bytesize=8, parity=parity, stopbits=stopbits, timeout=2
    )
    print(f"Connecting (RTU) to {port} {baudrate} 8{parity}{stopbits} (unit {unit_id}) ...")
    if not client.connect():
        print("  -> NO CONNECTION. Check the RS-485 wiring/adapter, that Modbus is")
        print("     enabled (MENNEKES Config Tool + DIP S1-4 = ON), and the serial")
        print("     parameters (try 8N1 if 8N2 fails).")
        return 1
    print("  -> connected. Dumping registers.\n")
    # Compact 2.0s registers live in 0x0100..0x0DFF; scan a useful window.
    _dump(client, unit_id, start, count)
    client.close()
    _tips()
    return 0


def _tips() -> None:
    print("Tip: (un)plug the car and re-run to see which register changes -> that's")
    print("the CP/charging state (reg_cp_state). For the Compact 2.0s the control")
    print("registers are 0x0302 (current setpoint), 0x0D05 (release), 0x0D00")
    print("(heartbeat). Confirm these against your unit before relying on them.")
