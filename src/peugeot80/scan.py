"""Probe a Mennekes wallbox over Modbus TCP to identify model and registers."""

from __future__ import annotations

from pymodbus.client import ModbusTcpClient


def scan(host: str, port: int = 502, unit_id: int = 1, start: int = 0, count: int = 200) -> int:
    """Connect to the wallbox and dump holding + input registers.

    Returns a process exit code (0 = reachable, 1 = not reachable).
    """
    client = ModbusTcpClient(host, port=port, timeout=5)
    print(f"Connecting to {host}:{port} (unit {unit_id}) ...")
    if not client.connect():
        print("  -> NO CONNECTION. This is likely an AMTRON Start/Compact (no Modbus")
        print("     TCP), or the IP/port is wrong, or Modbus TCP is not enabled.")
        print("     Use the 'cloud_delayed' charger provider instead.")
        return 1

    print("  -> connected. A reachable Modbus TCP server means an AMTRON")
    print("     Xtra/Premium (HCC3). Dumping registers so you can identify the")
    print("     CP-state and HEMS current-limit registers.\n")

    for label, reader in (
        ("HOLDING", client.read_holding_registers),
        ("INPUT", client.read_input_registers),
    ):
        print(f"== {label} registers {start}..{start + count - 1} ==")
        addr = start
        while addr < start + count:
            chunk = min(8, start + count - addr)
            rr = reader(addr, count=chunk, slave=unit_id)
            if rr.isError():
                addr += chunk
                continue
            for i, value in enumerate(rr.registers):
                a = addr + i
                ascii_hint = chr(value) if 32 <= value < 127 else ""
                print(f"  [{a:5d}] {value:6d}  0x{value:04X}  {ascii_hint}")
            addr += chunk
        print()

    client.close()
    print("Tip: look for a register that changes when you (un)plug the car (CP")
    print("state) and one near 1000/1002 for the HEMS current/power limit, then")
    print("set reg_cp_state / reg_hems_current_limit in config.yaml accordingly.")
    return 0
