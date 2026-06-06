"""State-of-charge providers."""

from __future__ import annotations

from .base import SocProvider, SocReading


def build_soc_provider(cfg: dict) -> SocProvider:
    provider = cfg.get("provider", "cloud")
    if provider == "cloud":
        from .cloud import CloudSocProvider

        return CloudSocProvider(cfg.get("cloud", {}))
    if provider == "obd":
        from .obd import ObdSocProvider

        return ObdSocProvider(cfg.get("obd", {}))
    raise ValueError(f"unknown soc provider: {provider!r}")


__all__ = ["SocProvider", "SocReading", "build_soc_provider"]
