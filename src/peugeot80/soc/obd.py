"""SoC via an OBD-II dongle (realtime).

The e-2008 (eCMP) does not expose SoC through a standard OBD PID; it lives in a
manufacturer-specific BMS command. The mode/PID/header/formula are therefore
configurable -- verify them for your vehicle before relying on the cutoff.

Requires the optional dependency: ``pip install "peugeot80[obd]"``.
"""

from __future__ import annotations

import logging

from .base import SocProvider, SocReading

_LOG = logging.getLogger(__name__)


class ObdSocProvider(SocProvider):
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self._conn = None

    def _connect(self):
        if self._conn is not None:
            return self._conn
        try:
            import obd  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                'OBD support not installed. Run: pip install "peugeot80[obd]"'
            ) from exc

        port = self.cfg.get("port")
        baudrate = self.cfg.get("baudrate")
        self._obd = obd
        self._conn = obd.OBD(portstr=port, baudrate=baudrate, fast=False)
        if not self._conn.is_connected():
            raise RuntimeError(f"could not open OBD connection on {port!r}")
        return self._conn

    def _soc_command(self):
        obd = self._obd
        mode = self.cfg.get("soc_mode", "22")
        pid = self.cfg.get("soc_pid", "")
        header = self.cfg.get("soc_header")
        raw = (mode + pid).strip()
        cmd = obd.OBDCommand(
            "EV_SOC",
            "EV state of charge",
            raw.encode(),
            0,
            lambda messages: _decode(messages, self.cfg.get("soc_formula", "A")),
            header=header.encode() if header else None,
        )
        return cmd

    def read(self) -> SocReading:
        conn = self._connect()
        resp = conn.query(self._soc_command(), force=True)
        if resp.is_null() or resp.value is None:
            raise RuntimeError("empty OBD response for SoC")
        return SocReading(percent=float(resp.value), charging=None)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _decode(messages, formula: str):
    if not messages or not messages[0].data:
        return None
    data = messages[0].data
    # Expose bytes A, B, C... to the formula, like classic OBD docs.
    scope = {chr(ord("A") + i): data[i] for i in range(min(len(data), 8))}
    try:
        return eval(formula, {"__builtins__": {}}, scope)  # noqa: S307 - trusted config
    except Exception:  # pragma: no cover - bad formula
        _LOG.exception("failed to evaluate soc_formula %r", formula)
        return None
