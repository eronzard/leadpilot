"""Slack notifications (incoming webhook) with a log-only fallback. De-duplicated per lead+channel."""

from __future__ import annotations

import time
from typing import Optional

import httpx

from ..db import Database
from . import PermanentIntegrationError, TransientIntegrationError


class Notifier:
    def __init__(self, db: Database, slack_webhook_url: str = "", client: Optional[httpx.Client] = None):
        self.db = db
        self.url = slack_webhook_url
        self.http = client or httpx.Client(timeout=10.0)

    def send(self, lead_id: Optional[str], channel: str, text: str) -> bool:
        """Returns True if delivered to Slack, False if only logged. Safe to call twice."""
        if lead_id and self.db.query_one(
            "SELECT 1 FROM notifications WHERE lead_id = ? AND channel = ?", (lead_id, channel)
        ):
            return True
        delivered = False
        if self.url:
            try:
                resp = self.http.post(self.url, json={"text": f"*{channel}*  {text}"})
            except httpx.TransportError as exc:
                raise TransientIntegrationError(f"Slack network error: {exc}") from exc
            if resp.status_code == 429 or resp.status_code >= 500:
                raise TransientIntegrationError(f"Slack {resp.status_code}")
            if resp.status_code >= 400:
                raise PermanentIntegrationError(f"Slack {resp.status_code}: {resp.text[:200]}")
            delivered = True
        self.db.execute(
            "INSERT OR IGNORE INTO notifications (lead_id, channel, text, delivered, ts) VALUES (?, ?, ?, ?, ?)",
            (lead_id, channel, text, int(delivered), time.time()),
        )
        return delivered
