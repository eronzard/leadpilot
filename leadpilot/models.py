"""Typed data flowing through the pipeline."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Intent = Literal[
    "sales_inquiry",
    "support_request",
    "partnership",
    "job_application",
    "vendor_pitch",
    "spam",
    "other",
]
Timeline = Literal["immediate", "this_quarter", "later", "unknown"]
CompanySize = Literal["solo", "2-10", "11-50", "51-200", "201-1000", "1000+", "unknown"]
Tier = Literal["hot", "warm", "cold", "support", "other", "spam"]


class LeadIn(BaseModel):
    """Payload accepted by POST /webhooks/lead (web forms, n8n, Zapier, email parsers)."""

    name: str = Field(default="", max_length=200)
    email: str = Field(max_length=320)
    company: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=500)
    phone: str = Field(default="", max_length=50)
    message: str = Field(min_length=1, max_length=10_000)
    source: str = Field(default="web_form", max_length=50)
    utm: dict[str, str] = Field(default_factory=dict)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v


class Enrichment(BaseModel):
    email_domain: str
    is_free_email: bool
    is_disposable_email: bool
    company_domain: Optional[str] = None
    company_guess: Optional[str] = None
    website_title: Optional[str] = None
    website_description: Optional[str] = None
    prior_leads_from_contact: int = 0


class Qualification(BaseModel):
    """Structured facts extracted by the qualifier (Claude or rules)."""

    intent: Intent
    services: list[str] = Field(default_factory=list)
    budget_usd: Optional[int] = None
    timeline: Timeline = "unknown"
    company_size: CompanySize = "unknown"
    industry: str = "unknown"
    is_decision_maker: Optional[bool] = None
    fit_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    language: str = "en"
    summary: str = ""
    personal_note: str = ""
    reasons: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


class QualifierResult(BaseModel):
    qualification: Qualification
    provider: str
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    fallback_reason: Optional[str] = None


class Decision(BaseModel):
    tier: Tier
    score: int
    needs_review: bool
    review_reasons: list[str] = Field(default_factory=list)
    create_deal: bool = False
    notify_channel: Optional[str] = None
    auto_send_reply: bool = False


class Reply(BaseModel):
    subject: str
    body: str
