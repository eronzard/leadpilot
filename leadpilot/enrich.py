"""Normalization, idempotency keys and lightweight enrichment.

Enrichment is deliberately offline-first: email-domain analysis needs no network, and the
optional website fetch is time-boxed and never fails the pipeline.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
from typing import Optional
from urllib.parse import urlparse

import httpx

from .models import Enrichment, LeadIn

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com", "outlook.com",
    "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.de", "mail.com", "yandex.com", "zoho.com", "hey.com", "fastmail.com",
}
DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com", "temp-mail.org",
    "trashmail.com", "yopmail.com", "sharklasers.com", "getnada.com", "dispostable.com",
}

_WS = re.compile(r"\s+")


def normalize(lead: LeadIn) -> LeadIn:
    """Trim whitespace, canonicalise website URL and name casing. Pure function."""
    website = lead.website.strip()
    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website
    name = _WS.sub(" ", lead.name).strip()
    if name.isupper() or name.islower():
        name = name.title()
    return lead.model_copy(update={
        "name": name,
        "company": _WS.sub(" ", lead.company).strip(),
        "website": website.rstrip("/"),
        "phone": re.sub(r"[^\d+]", "", lead.phone),
        "message": lead.message.strip(),
        "source": lead.source.strip().lower() or "web_form",
    })


def idempotency_key(lead: LeadIn) -> str:
    """Stable key for "the same submission": a double-clicked form or a webhook redelivery
    produces the same key, so it is processed exactly once."""
    basis = f"{lead.email.lower()}|{_WS.sub(' ', lead.message.lower()).strip()}"
    return hashlib.sha256(basis.encode()).hexdigest()


def _guess_company(domain: str) -> str:
    label = domain.split(".")[0]
    return " ".join(part.capitalize() for part in re.split(r"[-_]", label) if part)


def _is_public_host(host: str) -> bool:
    """SSRF guard: only fetch hosts that resolve exclusively to public IPs."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def fetch_site_meta(url: str, timeout: float = 3.0) -> tuple[Optional[str], Optional[str]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None, None
    if not _is_public_host(parsed.hostname):
        return None, None
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=False,
                         headers={"User-Agent": "LeadPilot/1.0 (+lead enrichment)"})
        text = resp.text[:200_000]
    except httpx.HTTPError:
        return None, None
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', text, re.I | re.S)
    clean = lambda m: html.unescape(_WS.sub(" ", m.group(1)).strip())[:300] if m else None  # noqa: E731
    return clean(title), clean(desc)


def enrich(lead: LeadIn, prior_leads: int = 0, fetch_website: bool = False) -> Enrichment:
    domain = lead.email.rsplit("@", 1)[1]
    is_free = domain in FREE_EMAIL_DOMAINS
    company_domain: Optional[str] = None
    if lead.website:
        company_domain = (urlparse(lead.website).hostname or "").removeprefix("www.") or None
    elif not is_free:
        company_domain = domain

    title = description = None
    if fetch_website and company_domain:
        title, description = fetch_site_meta(lead.website or f"https://{company_domain}")

    return Enrichment(
        email_domain=domain,
        is_free_email=is_free,
        is_disposable_email=domain in DISPOSABLE_EMAIL_DOMAINS,
        company_domain=company_domain,
        company_guess=lead.company or (_guess_company(company_domain) if company_domain else None),
        website_title=title,
        website_description=description,
        prior_leads_from_contact=prior_leads,
    )
