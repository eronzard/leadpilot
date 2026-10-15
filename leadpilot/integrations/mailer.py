"""Outbound email. 'outbox' records the message only (safe default); 'smtp' really sends it."""

from __future__ import annotations

import smtplib
import ssl
import time
from email.message import EmailMessage

from ..db import Database
from ..settings import Settings
from . import PermanentIntegrationError, TransientIntegrationError


class Mailer:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.s = settings

    def send(self, lead_id: str, to: str, subject: str, body: str) -> str:
        """Send once per lead. Returns the transport used."""
        if self.db.query_one("SELECT 1 FROM outbox WHERE lead_id = ?", (lead_id,)):
            return "already-sent"
        transport = "outbox"
        if self.s.mailer == "smtp":
            transport = "smtp"
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = self.s.smtp_from, to, subject
            msg.set_content(body)
            try:
                with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20) as smtp:
                    smtp.starttls(context=ssl.create_default_context())
                    if self.s.smtp_user:
                        smtp.login(self.s.smtp_user, self.s.smtp_password)
                    smtp.send_message(msg)
            except smtplib.SMTPAuthenticationError as exc:
                raise PermanentIntegrationError(f"SMTP auth failed: {exc}") from exc
            except (smtplib.SMTPException, OSError) as exc:
                raise TransientIntegrationError(f"SMTP error: {exc}") from exc
        self.db.execute(
            "INSERT OR IGNORE INTO outbox (lead_id, recipient, subject, body, transport, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lead_id, to, subject, body, transport, time.time()),
        )
        return transport
