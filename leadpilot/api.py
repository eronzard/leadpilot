"""HTTP API: the intake webhook, the review queue and the dashboard."""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import __version__, metrics
from .app import App, build
from .db import new_id
from .enrich import idempotency_key, normalize
from .models import LeadIn
from .settings import ROOT

STATIC = Path(__file__).parent / "static"


class RateLimiter:
    """Sliding-window limiter per client IP. Keeps a public form from being used as a cannon."""

    def __init__(self, limit: int = 30, window: float = 60.0):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


class ApproveBody(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


class DismissBody(BaseModel):
    note: str = ""


def create_app(app_ctx: Optional[App] = None, start_worker: Optional[bool] = None) -> FastAPI:
    ctx = app_ctx or build()
    run_worker = ctx.settings.run_worker if start_worker is None else start_worker
    limiter = RateLimiter(limit=ctx.settings.rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if run_worker:
            ctx.worker.start()
        yield
        if run_worker:
            ctx.worker.stop()

    api = FastAPI(title="LeadPilot", version=__version__, lifespan=lifespan,
                  description="Inbound lead automation: intake webhook, qualification, routing, review queue.")
    api.state.ctx = ctx

    def require_admin(x_admin_token: str = Header(default="")) -> None:
        token = ctx.settings.admin_token
        if token and not hmac.compare_digest(x_admin_token, token):
            raise HTTPException(401, "missing or invalid X-Admin-Token")

    # ------------------------------------------------------------------ intake
    @api.post("/webhooks/lead", status_code=202)
    async def intake(request: Request,
                     x_leadpilot_signature: str = Header(default=""),
                     idempotency_key_header: str = Header(default="", alias="Idempotency-Key")):
        client_ip = request.client.host if request.client else "unknown"
        raw = await request.body()
        if len(raw) > 64_000:
            raise HTTPException(413, "payload too large")

        secret = ctx.settings.webhook_secret
        if secret:
            expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(x_leadpilot_signature, expected):
                raise HTTPException(401, "invalid signature")
        # Signed requests come from a trusted integration (n8n, Zapier) that may legitimately
        # replay a backlog from one IP, so the per-IP limit only applies to unsigned traffic.
        elif limiter.limit and not limiter.allow(client_ip):
            raise HTTPException(429, "rate limit exceeded")

        content_type = request.headers.get("content-type", "")
        try:
            if "application/x-www-form-urlencoded" in content_type:
                data: Any = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            else:
                data = json.loads(raw or b"{}")
            lead = normalize(LeadIn.model_validate(data))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(400, "body must be JSON or form-encoded")
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": json.loads(exc.json(include_url=False))})

        key = idempotency_key_header.strip() or idempotency_key(lead)
        lead_id = new_id()
        if not ctx.db.insert_lead_with_job(lead_id, key, lead.model_dump()):
            existing = ctx.db.get_lead_by_key(key)
            assert existing is not None
            ctx.db.log(existing["id"], "intake.duplicate", "duplicate submission ignored")
            return JSONResponse(status_code=200, content={
                "lead_id": existing["id"], "status": existing["status"], "duplicate": True})
        ctx.db.log(lead_id, "intake.received", f"lead received from {lead.source}",
                   data={"ip": client_ip, "idempotency_key": key[:16]})
        return {"lead_id": lead_id, "status": "queued", "duplicate": False}

    # ------------------------------------------------------------------ read API
    @api.get("/healthz")
    def healthz():
        queued = ctx.db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')")
        dead = ctx.db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'dead'")
        return {"ok": True, "version": __version__, "qualifier": ctx.qualifier.name, "crm": ctx.crm.name,
                "mailer": ctx.settings.mailer, "queue_depth": queued["n"] if queued else 0,
                "dead_letter": dead["n"] if dead else 0}

    @api.get("/api/leads")
    def list_leads(status: Optional[str] = None, tier: Optional[str] = None, limit: int = 100):
        sql = ("SELECT id, received_at, completed_at, source, email, name, company, status, tier, score, "
               "needs_review, review_reasons, reply_status, qualifier, cost_usd, error FROM leads")
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if tier:
            clauses.append("tier = ?")
            params.append(tier)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY received_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return ctx.db.query(sql, tuple(params))

    @api.get("/api/leads/{lead_id}")
    def get_lead(lead_id: str):
        lead = ctx.db.get_lead(lead_id)
        if lead is None:
            raise HTTPException(404, "lead not found")
        return {"lead": lead, "events": ctx.db.events(lead_id), "steps": ctx.db.step_timings(lead_id),
                "job": ctx.db.get_job(lead_id)}

    @api.get("/api/metrics")
    def get_metrics():
        return metrics.compute(ctx.db, ctx.settings.business)

    @api.get("/api/crm")
    def crm_view():
        return {"contacts": ctx.db.query("SELECT * FROM crm_contacts ORDER BY updated_at DESC LIMIT 200"),
                "deals": ctx.db.query("SELECT * FROM crm_deals ORDER BY created_at DESC LIMIT 200")}

    @api.get("/api/outbox")
    def outbox():
        return ctx.db.query("SELECT * FROM outbox ORDER BY sent_at DESC LIMIT 200")

    @api.get("/api/notifications")
    def notifications():
        return ctx.db.query("SELECT * FROM notifications ORDER BY ts DESC LIMIT 200")

    @api.get("/api/samples")
    def samples(n: int = 12):
        """Random leads from the eval dataset, for the dashboard's demo button."""
        path = ROOT / "evals" / "dataset.jsonl"
        if not path.exists():
            return []
        cases = [json.loads(line)["lead"] for line in path.read_text().splitlines() if line.strip()]
        return random.sample(cases, k=max(1, min(n, len(cases))))

    # ------------------------------------------------------------------ review actions
    @api.post("/api/leads/{lead_id}/approve", dependencies=[Depends(require_admin)])
    def approve(lead_id: str, body: ApproveBody):
        try:
            return ctx.pipeline.approve_reply(lead_id, body.subject, body.body)
        except LookupError:
            raise HTTPException(404, "lead not found")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @api.post("/api/leads/{lead_id}/dismiss", dependencies=[Depends(require_admin)])
    def dismiss(lead_id: str, body: DismissBody):
        try:
            return ctx.pipeline.dismiss(lead_id, body.note)
        except LookupError:
            raise HTTPException(404, "lead not found")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @api.post("/api/leads/{lead_id}/retry", dependencies=[Depends(require_admin)])
    def retry(lead_id: str):
        if not ctx.db.requeue_lead(lead_id):
            raise HTTPException(409, "lead is not in the dead-letter queue")
        ctx.db.update_lead(lead_id, status="queued", error=None)
        ctx.db.log(lead_id, "job.requeued", "manually re-queued from dead letter")
        return {"lead_id": lead_id, "status": "queued"}

    # ------------------------------------------------------------------ dashboard
    api.mount("/static", StaticFiles(directory=STATIC), name="static")

    @api.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return api
