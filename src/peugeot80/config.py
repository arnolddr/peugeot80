"""Configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class Config(dict):
    """Thin dict wrapper with dotted-path access and defaults."""

    def get_path(self, path: str, default: Any = None) -> Any:
        node: Any = self
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


DEFAULTS: dict[str, Any] = {
    "charge_limit": 80,
    "resume_below": 0,
    "hysteresis": 2,
    "poll_interval": 120,
    "poll_interval_charging": 30,
    "soc": {"provider": "cloud"},
    "charger": {"provider": "mennekes_modbus"},
    "logging": {"level": "INFO"},
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    return Config(_deep_merge(DEFAULTS, raw))
