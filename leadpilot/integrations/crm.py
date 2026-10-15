"""CRM adapters: a local SQLite CRM (default, zero setup) and HubSpot (private-app token)."""

from __future__ import annotations

import random
import time
from typing import Any, Optional, Protocol

import httpx

from ..db import Database, dumps, new_id
from . import PermanentIntegrationError, TransientIntegrationError


class CRM(Protocol):
    name: str

    def upsert_contact(self, email: str, name: str, company: str, properties: dict[str, Any]) -> str: ...

    def create_deal(self, lead_id: str, contact_id: str, name: str, amount: Optional[int]) -> str: ...


def _split_name(name: str) -> tuple[str, str]:
    parts = name.split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


class LocalCRM:
    """Stand-in CRM stored in SQLite, so the whole flow is demoable without accounts."""

    name = "local"

    def __init__(self, db: Database):
        self.db = db

    def upsert_contact(self, email: str, name: str, company: str, properties: dict[str, Any]) -> str:
        now = time.time()
        existing = self.db.query_one("SELECT id FROM crm_contacts WHERE email = ?", (email,))
        if existing:
            self.db.execute(
                "UPDATE crm_contacts SET name = COALESCE(NULLIF(?, ''), name), "
                "company = COALESCE(NULLIF(?, ''), company), properties = ?, updated_at = ? WHERE id = ?",
                (name, company, dumps(properties), now, existing["id"]),
            )
            return existing["id"]
        contact_id = "c_" + new_id()
        self.db.execute(
            "INSERT INTO crm_contacts (id, email, name, company, properties, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (contact_id, email, name, company, dumps(properties), now, now),
        )
        return contact_id

    def create_deal(self, lead_id: str, contact_id: str, name: str, amount: Optional[int]) -> str:
        existing = self.db.query_one("SELECT id FROM crm_deals WHERE lead_id = ?", (lead_id,))
        if existing:  # idempotent on lead_id
            return existing["id"]
        deal_id = "d_" + new_id()
        self.db.execute(
            "INSERT INTO crm_deals (id, contact_id, lead_id, name, amount, stage, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'qualified', ?)",
            (deal_id, contact_id, lead_id, name, amount, time.time()),
        )
        return deal_id


class HubSpotCRM:
    """HubSpot CRM v3. Contacts are upserted by email; deals are de-duplicated by a lead tag
    in the deal name so a retry after a lost response does not create a second deal."""

    name = "hubspot"
    BASE = "https://api.hubapi.com"

    def __init__(self, token: str, pipeline_id: str = "default", dealstage_id: str = "appointmentscheduled",
                 client: Optional[httpx.Client] = None):
        if not token:
            raise PermanentIntegrationError("HUBSPOT_TOKEN is not set")
        self.pipeline_id = pipeline_id
        self.dealstage_id = dealstage_id
        self.http = client or httpx.Client(
            base_url=self.BASE, timeout=15.0,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self.http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise TransientIntegrationError(f"HubSpot network error: {exc}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientIntegrationError(f"HubSpot {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise PermanentIntegrationError(f"HubSpot {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def upsert_contact(self, email: str, name: str, company: str, properties: dict[str, Any]) -> str:
        first, last = _split_name(name)
        props = {"email": email, "firstname": first, "lastname": last, "company": company}
        props.update({k: v for k, v in properties.items() if k in {"website", "phone"} and v})
        data = self._request("POST", "/crm/v3/objects/contacts/batch/upsert", json={
            "inputs": [{"idProperty": "email", "id": email,
                        "properties": {k: v for k, v in props.items() if v}}],
        })
        return str(data["results"][0]["id"])

    def create_deal(self, lead_id: str, contact_id: str, name: str, amount: Optional[int]) -> str:
        tag = f"[LP-{lead_id}]"
        found = self._request("POST", "/crm/v3/objects/deals/search", json={
            "filterGroups": [{"filters": [{"propertyName": "dealname", "operator": "CONTAINS_TOKEN",
                                           "value": tag}]}],
            "limit": 1,
        })
        if found.get("results"):
            return str(found["results"][0]["id"])
        props = {"dealname": f"{name} {tag}", "pipeline": self.pipeline_id, "dealstage": self.dealstage_id}
        if amount:
            props["amount"] = str(amount)
        data = self._request("POST", "/crm/v3/objects/deals", json={
            "properties": props,
            "associations": [{"to": {"id": contact_id},
                              "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]}],
        })
        return str(data["id"])


class FlakyCRM:
    """Chaos wrapper: raises a transient error with probability ``rate`` before each write.
    Used to prove the retry/checkpoint path works (LEADPILOT_FAULT_CRM_RATE)."""

    def __init__(self, inner: CRM, rate: float, rng: Optional[random.Random] = None):
        self.inner, self.rate, self.rng = inner, rate, rng or random.Random()
        self.name = f"{inner.name}+chaos"

    def _maybe_fail(self) -> None:
        if self.rng.random() < self.rate:
            raise TransientIntegrationError("injected fault: CRM returned 503")

    def upsert_contact(self, *args: Any, **kwargs: Any) -> str:
        self._maybe_fail()
        return self.inner.upsert_contact(*args, **kwargs)

    def create_deal(self, *args: Any, **kwargs: Any) -> str:
        self._maybe_fail()
        return self.inner.create_deal(*args, **kwargs)
