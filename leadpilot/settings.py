"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the app runs without python-dotenv. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    qualifier: str = "claude"
    model: str = "claude-opus-5-5"
    effort: str = "low"
    anthropic_api_key: str = ""
    db_path: str = "data/leadpilot.db"
    business_config: str = "config/business.yaml"
    webhook_secret: str = ""
    rate_limit_per_minute: int = 30
    admin_token: str = ""
    public_url: str = ""
    crm: str = "local"
    hubspot_token: str = ""
    hubspot_pipeline_id: str = "default"
    hubspot_dealstage_id: str = "appointmentscheduled"
    slack_webhook_url: str = ""
    mailer: str = "outbox"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    max_attempts: int = 4
    retry_base_seconds: float = 5.0
    enrich_fetch_website: bool = False
    fault_crm_rate: float = 0.0
    run_worker: bool = True
    business: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_qualifier(self) -> str:
        """Fall back to the rules engine when Claude is selected but no key is configured."""
        if self.qualifier == "claude" and not self.anthropic_api_key:
            return "rules"
        return self.qualifier


def load_business(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    with p.open() as fh:
        return yaml.safe_load(fh)


@lru_cache
def get_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    business_config = _env("LEADPILOT_BUSINESS_CONFIG", "config/business.yaml")
    return Settings(
        qualifier=_env("LEADPILOT_QUALIFIER", "claude").lower(),
        model=_env("LEADPILOT_MODEL", "claude-opus-5-5"),
        effort=_env("LEADPILOT_EFFORT", "low"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        db_path=_env("LEADPILOT_DB_PATH", "data/leadpilot.db"),
        business_config=business_config,
        webhook_secret=_env("LEADPILOT_WEBHOOK_SECRET"),
        rate_limit_per_minute=int(_env("LEADPILOT_RATE_LIMIT_PER_MINUTE", "30") or 30),
        admin_token=_env("LEADPILOT_ADMIN_TOKEN"),
        public_url=_env("LEADPILOT_PUBLIC_URL").rstrip("/"),
        crm=_env("LEADPILOT_CRM", "local").lower(),
        hubspot_token=_env("HUBSPOT_TOKEN"),
        hubspot_pipeline_id=_env("HUBSPOT_PIPELINE_ID", "default"),
        hubspot_dealstage_id=_env("HUBSPOT_DEALSTAGE_ID", "appointmentscheduled"),
        slack_webhook_url=_env("SLACK_WEBHOOK_URL"),
        mailer=_env("LEADPILOT_MAILER", "outbox").lower(),
        smtp_host=_env("SMTP_HOST"),
        smtp_port=int(_env("SMTP_PORT", "587") or 587),
        smtp_user=_env("SMTP_USER"),
        smtp_password=_env("SMTP_PASSWORD"),
        smtp_from=_env("SMTP_FROM"),
        max_attempts=int(_env("LEADPILOT_MAX_ATTEMPTS", "4") or 4),
        retry_base_seconds=float(_env("LEADPILOT_RETRY_BASE_SECONDS", "5") or 5),
        enrich_fetch_website=_env_bool("LEADPILOT_ENRICH_FETCH_WEBSITE"),
        fault_crm_rate=float(_env("LEADPILOT_FAULT_CRM_RATE", "0") or 0),
        run_worker=_env_bool("LEADPILOT_RUN_WORKER", True),
        business=load_business(business_config),
    )
