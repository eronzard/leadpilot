"""Wiring: build the object graph once, shared by the API, scripts and evals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .db import Database
from .integrations.crm import CRM, FlakyCRM, HubSpotCRM, LocalCRM
from .integrations.mailer import Mailer
from .integrations.notify import Notifier
from .pipeline import Pipeline
from .qualify import Qualifier, make_qualifier
from .settings import Settings, get_settings
from .worker import Worker


@dataclass
class App:
    settings: Settings
    db: Database
    qualifier: Qualifier
    crm: CRM
    notifier: Notifier
    mailer: Mailer
    pipeline: Pipeline
    worker: Worker


def build(settings: Optional[Settings] = None, db: Optional[Database] = None,
          qualifier: Optional[Qualifier] = None, crm: Optional[CRM] = None) -> App:
    s = settings or get_settings()
    db = db or Database(s.db_path)
    if qualifier is None:
        kind = s.effective_qualifier
        kwargs = {"model": s.model, "effort": s.effort, "api_key": s.anthropic_api_key} if kind == "claude" else {}
        qualifier = make_qualifier(kind, s.business, **kwargs)
    if crm is None:
        crm = (HubSpotCRM(s.hubspot_token, s.hubspot_pipeline_id, s.hubspot_dealstage_id)
               if s.crm == "hubspot" else LocalCRM(db))
        if s.fault_crm_rate > 0:
            crm = FlakyCRM(crm, s.fault_crm_rate)
    notifier = Notifier(db, s.slack_webhook_url)
    mailer = Mailer(db, s)
    pipeline = Pipeline(db, s.business, qualifier, crm, notifier, mailer, fetch_website=s.enrich_fetch_website,
                        public_url=s.public_url)
    worker = Worker(db, pipeline, notifier, max_attempts=s.max_attempts, retry_base_seconds=s.retry_base_seconds)
    return App(s, db, qualifier, crm, notifier, mailer, pipeline, worker)
