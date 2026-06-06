"""Alerting: log every alert, optionally POST to a webhook (ntfy/HA/Slack...)."""

from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: dict | None = None) -> None:
        cfg = cfg or {}
        self.webhook = cfg.get("webhook")
        self.timeout = cfg.get("timeout", 10)

    def alert(self, message: str) -> None:
        _LOG.error("ALERT: %s", message)
        if not self.webhook:
            return
        try:
            import requests

            requests.post(self.webhook, json={"text": message}, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - alerting must never crash the loop
            _LOG.warning("webhook notify failed: %s", exc)

    def info(self, message: str) -> None:
        _LOG.info("%s", message)
