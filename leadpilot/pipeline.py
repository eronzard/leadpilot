"""The end-to-end pipeline, as checkpointed steps.

    enrich -> qualify -> decide -> crm_contact -> crm_deal -> draft_reply -> send_reply -> notify

Every step's output is saved to the ``steps`` table. When a job is retried (say the CRM
returned a 503 on step 5), steps 1-4 are replayed from their checkpoints instead of re-run:
no second LLM bill, no duplicate CRM contact, no second Slack ping.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .db import Database
from .enrich import enrich
from .integrations.crm import CRM
from .integrations.mailer import Mailer
from .integrations.notify import Notifier
from .models import Decision, Enrichment, LeadIn, QualifierResult, Reply
from .qualify import Qualifier
from .replies import draft_reply
from .routing import decide


class Pipeline:
    def __init__(self, db: Database, business: dict[str, Any], qualifier: Qualifier, crm: CRM,
                 notifier: Notifier, mailer: Mailer, fetch_website: bool = False, public_url: str = ""):
        self.db = db
        self.business = business
        self.qualifier = qualifier
        self.crm = crm
        self.notifier = notifier
        self.mailer = mailer
        self.fetch_website = fetch_website
        self.public_url = public_url

    def _step(self, lead_id: str, name: str, done: dict[str, Any], attempt: int,
              fn: Callable[[], Any]) -> Any:
        if name in done:
            return done[name]
        started = time.perf_counter()
        output = fn()
        duration = (time.perf_counter() - started) * 1000
        self.db.save_step(lead_id, name, output, duration, attempt)
        self.db.log(lead_id, f"step.{name}", f"{name} ok in {duration:.0f} ms")
        done[name] = output
        return output

    def process(self, lead_id: str, attempt: int = 1) -> dict[str, Any]:
        row = self.db.get_lead(lead_id)
        if row is None:
            raise LookupError(f"lead {lead_id} not found")
        lead = LeadIn.model_validate(row["payload"])
        done = self.db.get_steps(lead_id)
        if done:
            self.db.log(lead_id, "pipeline.resume", f"attempt {attempt}: resuming after {len(done)} checkpointed steps")
        self.db.update_lead(lead_id, status="processing")

        # 1. Enrich
        enrichment = Enrichment.model_validate(self._step(lead_id, "enrich", done, attempt, lambda: enrich(
            lead,
            prior_leads=self.db.count_prior_leads(lead.email, row["received_at"], lead_id),
            fetch_website=self.fetch_website,
        ).model_dump()))

        # 2. Qualify (the only LLM call)
        result = QualifierResult.model_validate(self._step(
            lead_id, "qualify", done, attempt, lambda: self.qualifier.qualify(lead, enrichment).model_dump()))
        if result.fallback_reason:
            self.db.log(lead_id, "qualify.fallback", f"rules fallback used: {result.fallback_reason}", level="warn")
        q = result.qualification

        # 3. Decide (deterministic)
        decision = Decision.model_validate(self._step(
            lead_id, "decide", done, attempt, lambda: decide(result, enrichment, self.business).model_dump()))
        self.db.update_lead(
            lead_id, enrichment=enrichment.model_dump(), qualification=q.model_dump(),
            qualifier=result.provider + (f" ({result.model})" if result.model else ""),
            decision=decision.model_dump(), tier=decision.tier, score=decision.score,
            needs_review=int(decision.needs_review), review_reasons=decision.review_reasons,
            cost_usd=result.cost_usd,
        )

        # 4-5. CRM
        contact_id: Optional[str] = None
        deal_id: Optional[str] = None
        if decision.tier in {"hot", "warm", "cold", "support"}:
            contact_id = self._step(lead_id, "crm_contact", done, attempt, lambda: self.crm.upsert_contact(
                lead.email, lead.name, lead.company or (enrichment.company_guess or ""),
                {"website": lead.website, "phone": lead.phone, "tier": decision.tier,
                 "score": decision.score, "summary": q.summary},
            ))
            self.db.update_lead(lead_id, crm_contact_id=contact_id)
        if decision.create_deal and contact_id:
            company = lead.company or enrichment.company_guess or lead.name or lead.email
            deal_id = self._step(lead_id, "crm_deal", done, attempt, lambda: self.crm.create_deal(
                lead_id, contact_id, f"{company} - {', '.join(q.services) or 'automation'}", q.budget_usd))
            self.db.update_lead(lead_id, crm_deal_id=deal_id)

        # 6-7. Reply
        reply_data = self._step(lead_id, "draft_reply", done, attempt, lambda: (
            r.model_dump() if (r := draft_reply(lead, q, decision, self.business)) else None))
        reply = Reply.model_validate(reply_data) if reply_data else None
        reply_status = "none"
        if reply and decision.auto_send_reply:
            self._step(lead_id, "send_reply", done, attempt,
                       lambda: self.mailer.send(lead_id, lead.email, reply.subject, reply.body))
            reply_status = "sent"
        elif reply:
            reply_status = "draft"

        # 8. Notify
        if decision.notify_channel:
            text = self._notification_text(lead_id, lead, decision, result)
            self._step(lead_id, "notify", done, attempt,
                       lambda: self.notifier.send(lead_id, decision.notify_channel, text))

        now = time.time()
        awaiting_human = decision.needs_review or reply_status == "draft"
        status = "needs_review" if awaiting_human else "done"
        fields: dict[str, Any] = {"status": status, "reply": reply.model_dump() if reply else None,
                                  "reply_status": reply_status, "completed_at": now, "error": None}
        if reply_status == "sent" and not row.get("first_response_at"):
            fields["first_response_at"] = now
        self.db.update_lead(lead_id, **fields)
        self.db.log(lead_id, "pipeline.done",
                    f"tier={decision.tier} score={decision.score} reply={reply_status} status={status}",
                    data={"review_reasons": decision.review_reasons})
        return self.db.get_lead(lead_id) or {}

    def _notification_text(self, lead_id: str, lead: LeadIn, decision: Decision, result: QualifierResult) -> str:
        q = result.qualification
        budget = f"${q.budget_usd:,}" if q.budget_usd else "n/a"
        bits = [f"{decision.tier.upper()} lead ({decision.score}/100): {lead.name or lead.email}"
                f"{' @ ' + lead.company if lead.company else ''}",
                f"Summary: {q.summary}", f"Budget: {budget} | Timeline: {q.timeline}"]
        if decision.review_reasons:
            bits.append("Needs review: " + "; ".join(decision.review_reasons))
        bits.append(f"Open: {self.public_url}/#lead={lead_id}" if self.public_url else f"Lead id: {lead_id}")
        return "\n".join(bits)

    # ------------------------------------------------------------------ human-in-the-loop
    def approve_reply(self, lead_id: str, subject: Optional[str] = None, body: Optional[str] = None) -> dict:
        row = self.db.get_lead(lead_id)
        if row is None:
            raise LookupError(lead_id)
        if row["status"] != "needs_review":
            raise ValueError(f"lead is {row['status']}, not awaiting review")
        reply = row.get("reply")
        now = time.time()
        fields: dict[str, Any] = {"status": "done", "needs_review": 0,
                                  "human_touches": row["human_touches"] + 1}
        if reply:
            subject = subject or reply["subject"]
            body = body or reply["body"]
            self.mailer.send(lead_id, row["email"], subject, body)
            fields.update(reply={"subject": subject, "body": body}, reply_status="sent")
            if not row.get("first_response_at"):
                fields["first_response_at"] = now
        self.db.update_lead(lead_id, **fields)
        self.db.log(lead_id, "review.approved", "reviewer approved" + (" and sent reply" if reply else ""))
        return self.db.get_lead(lead_id) or {}

    def dismiss(self, lead_id: str, note: str = "") -> dict:
        row = self.db.get_lead(lead_id)
        if row is None:
            raise LookupError(lead_id)
        if row["status"] != "needs_review":
            raise ValueError(f"lead is {row['status']}, not awaiting review")
        self.db.update_lead(lead_id, status="done", needs_review=0,
                            reply_status="skipped" if row.get("reply") else row["reply_status"],
                            human_touches=row["human_touches"] + 1)
        self.db.log(lead_id, "review.dismissed", "reviewer dismissed" + (f": {note}" if note else ""))
        return self.db.get_lead(lead_id) or {}
