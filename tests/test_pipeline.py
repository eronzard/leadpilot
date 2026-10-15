import hashlib
import hmac
import json
import random

from fastapi.testclient import TestClient

from leadpilot.api import create_app
from leadpilot.integrations import PermanentIntegrationError
from leadpilot.integrations.crm import FlakyCRM, LocalCRM
from leadpilot.qualify import RulesQualifier

from .conftest import BUSINESS, HOT_LEAD, WARM_LEAD


def test_hot_lead_end_to_end(client, ctx):
    r = client.post("/webhooks/lead", json=HOT_LEAD)
    assert r.status_code == 202
    lead_id = r.json()["lead_id"]
    assert ctx.worker.drain() == 1

    lead = client.get(f"/api/leads/{lead_id}").json()["lead"]
    assert lead["tier"] == "hot"
    assert lead["status"] == "needs_review"          # hot reply waits for approval
    assert lead["reply_status"] == "draft"
    assert lead["crm_contact_id"] and lead["crm_deal_id"]
    assert BUSINESS["business"]["booking_link"] in lead["reply"]["body"]

    notes = client.get("/api/notifications").json()
    assert [n["channel"] for n in notes] == ["#sales-hot"]
    assert client.get("/api/outbox").json() == []    # nothing sent yet

    approved = client.post(f"/api/leads/{lead_id}/approve", json={}).json()
    assert approved["status"] == "done" and approved["reply_status"] == "sent"
    assert approved["first_response_at"] and approved["human_touches"] == 1
    assert len(client.get("/api/outbox").json()) == 1

    # approving twice is rejected, not double-sent
    assert client.post(f"/api/leads/{lead_id}/approve", json={}).status_code == 409


def test_warm_lead_is_zero_touch(client, ctx):
    lead_id = client.post("/webhooks/lead", json=WARM_LEAD).json()["lead_id"]
    ctx.worker.drain()
    lead = ctx.db.get_lead(lead_id)
    assert lead["tier"] == "warm" and lead["status"] == "done" and lead["reply_status"] == "sent"
    m = client.get("/api/metrics").json()
    assert m["zero_touch_rate"] == 1.0 and m["before_after"]["hours_saved"] > 0


def test_duplicate_submission_processed_once(client, ctx):
    first = client.post("/webhooks/lead", json=HOT_LEAD).json()
    again = client.post("/webhooks/lead", json={**HOT_LEAD, "message": HOT_LEAD["message"] + "  "})
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["lead_id"] == first["lead_id"]
    assert ctx.worker.drain() == 1


def test_form_encoded_body_accepted(client):
    r = client.post("/webhooks/lead", data={"email": "x@acme.example", "message": "Need a Zapier flow"})
    assert r.status_code == 202


def test_validation_error(client):
    r = client.post("/webhooks/lead", json={"email": "nope", "message": ""})
    assert r.status_code == 422


def test_signature_required_when_secret_set(make_ctx):
    ctx = make_ctx(webhook_secret="s3cret")
    with TestClient(create_app(ctx, start_worker=False)) as c:
        body = json.dumps(WARM_LEAD).encode()
        assert c.post("/webhooks/lead", content=body,
                      headers={"content-type": "application/json"}).status_code == 401
        sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        ok = c.post("/webhooks/lead", content=body,
                    headers={"content-type": "application/json", "X-LeadPilot-Signature": sig})
        assert ok.status_code == 202


def test_admin_token_protects_actions(make_ctx):
    ctx = make_ctx(admin_token="tok")
    with TestClient(create_app(ctx, start_worker=False)) as c:
        lead_id = c.post("/webhooks/lead", json=HOT_LEAD).json()["lead_id"]
        ctx.worker.drain()
        assert c.post(f"/api/leads/{lead_id}/approve", json={}).status_code == 401
        assert c.post(f"/api/leads/{lead_id}/approve", json={},
                      headers={"X-Admin-Token": "tok"}).status_code == 200


class CountingQualifier(RulesQualifier):
    def __init__(self):
        super().__init__(BUSINESS)
        self.calls = 0

    def qualify(self, lead, enrichment):
        self.calls += 1
        return super().qualify(lead, enrichment)


def test_flaky_crm_retries_resume_from_checkpoint(make_ctx):
    qualifier = CountingQualifier()
    ctx = make_ctx(qualifier=qualifier, max_attempts=6)
    # Seeded so the first two CRM writes fail and later ones succeed.
    flaky = FlakyCRM(LocalCRM(ctx.db), rate=1.0)
    ctx.pipeline.crm = flaky
    with TestClient(create_app(ctx, start_worker=False)) as c:
        lead_id = c.post("/webhooks/lead", json=HOT_LEAD).json()["lead_id"]
        ctx.worker.run_once()
        ctx.worker.run_once()
        assert ctx.db.get_job(lead_id)["attempts"] == 2
        assert ctx.db.get_lead(lead_id)["status"] == "queued"
        flaky.rate = 0.0  # CRM recovers
        ctx.worker.run_once()
    lead = ctx.db.get_lead(lead_id)
    assert lead["status"] == "needs_review" and lead["crm_deal_id"]
    assert qualifier.calls == 1  # the LLM step was checkpointed, not re-billed
    kinds = [e["kind"] for e in ctx.db.events(lead_id)]
    assert kinds.count("job.retry") == 2 and "pipeline.resume" in kinds
    assert len(ctx.db.query("SELECT * FROM crm_deals")) == 1


def test_exhausted_retries_dead_letter_and_alert(make_ctx):
    ctx = make_ctx(max_attempts=2)
    ctx.pipeline.crm = FlakyCRM(LocalCRM(ctx.db), rate=1.0, rng=random.Random(0))
    with TestClient(create_app(ctx, start_worker=False)) as c:
        lead_id = c.post("/webhooks/lead", json=HOT_LEAD).json()["lead_id"]
        ctx.worker.drain()
        assert ctx.db.get_lead(lead_id)["status"] == "dead"
        assert c.get("/healthz").json()["dead_letter"] == 1
        alerts = [n for n in c.get("/api/notifications").json() if n["channel"] == "#ops-alerts"]
        assert len(alerts) == 1

        # Fix the CRM, replay from the dashboard.
        ctx.pipeline.crm = LocalCRM(ctx.db)
        assert c.post(f"/api/leads/{lead_id}/retry").status_code == 200
        ctx.worker.drain()
        assert ctx.db.get_lead(lead_id)["status"] == "needs_review"


class BrokenCRM(LocalCRM):
    def upsert_contact(self, *a, **k):
        raise PermanentIntegrationError("HubSpot 401: invalid token")


def test_permanent_error_skips_retries(make_ctx):
    ctx = make_ctx()
    ctx.pipeline.crm = BrokenCRM(ctx.db)
    with TestClient(create_app(ctx, start_worker=False)) as c:
        lead_id = c.post("/webhooks/lead", json=WARM_LEAD).json()["lead_id"]
        ctx.worker.drain()
    job = ctx.db.get_job(lead_id)
    assert job["status"] == "dead" and job["attempts"] == 1


def test_spam_gets_no_crm_record_or_reply(client, ctx):
    lead_id = client.post("/webhooks/lead", json={
        "email": "win@promo.example",
        "message": "Earn $5000 a day with our bitcoin bot! Click here http://a.example http://b.example http://c.example",
    }).json()["lead_id"]
    ctx.worker.drain()
    lead = ctx.db.get_lead(lead_id)
    assert lead["tier"] == "spam" and lead["status"] == "done"
    assert lead["crm_contact_id"] is None and lead["reply"] is None


def test_returning_contact_is_upserted_not_duplicated(client, ctx):
    client.post("/webhooks/lead", json=WARM_LEAD)
    client.post("/webhooks/lead", json={**WARM_LEAD, "message": "Following up - any update on a quote?"})
    ctx.worker.drain()
    assert len(ctx.db.query("SELECT * FROM crm_contacts")) == 1


def test_unsigned_traffic_is_rate_limited(make_ctx):
    ctx = make_ctx(rate_limit_per_minute=3)
    with TestClient(create_app(ctx, start_worker=False)) as c:
        codes = [c.post("/webhooks/lead", json={**WARM_LEAD, "message": f"msg {i}"}).status_code for i in range(5)]
    assert codes == [202, 202, 202, 429, 429]


def test_signed_traffic_bypasses_rate_limit(make_ctx):
    ctx = make_ctx(rate_limit_per_minute=1, webhook_secret="k")
    with TestClient(create_app(ctx, start_worker=False)) as c:
        for i in range(4):
            body = json.dumps({**WARM_LEAD, "message": f"backlog item {i}"}).encode()
            sig = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
            r = c.post("/webhooks/lead", content=body,
                       headers={"content-type": "application/json", "X-LeadPilot-Signature": sig})
            assert r.status_code == 202
