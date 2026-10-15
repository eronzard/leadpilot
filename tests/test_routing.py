from leadpilot.models import Enrichment, Qualification, QualifierResult
from leadpilot.routing import decide

from .conftest import BUSINESS


def result(**kw) -> QualifierResult:
    q = {"intent": "sales_inquiry", "fit_score": 90, "confidence": 0.9, "budget_usd": 10000,
         "timeline": "immediate"}
    q.update(kw)
    return QualifierResult(qualification=Qualification(**q), provider="test")


ENR = Enrichment(email_domain="acme.example", is_free_email=False, is_disposable_email=False)


def test_hot_lead_gets_deal_and_alert_but_reply_needs_approval():
    d = decide(result(), ENR, BUSINESS)
    assert d.tier == "hot" and d.create_deal and d.notify_channel == "#sales-hot"
    assert not d.auto_send_reply  # hot replies are approved by a human
    assert not d.needs_review


def test_warm_lead_auto_replies():
    d = decide(result(fit_score=60), ENR, BUSINESS)
    assert d.tier == "warm" and d.auto_send_reply and d.create_deal


def test_model_cannot_fast_track_a_disposable_email():
    disposable = ENR.model_copy(update={"is_disposable_email": True})
    assert decide(result(fit_score=100), disposable, BUSINESS).tier == "cold"


def test_budget_below_minimum_is_never_hot():
    assert decide(result(fit_score=100, budget_usd=500), ENR, BUSINESS).tier == "warm"


def test_later_timeline_is_never_hot():
    assert decide(result(fit_score=100, timeline="later"), ENR, BUSINESS).tier == "warm"


def test_injection_forces_review_and_blocks_auto_send():
    d = decide(result(fit_score=60, red_flags=["prompt_injection_attempt"]), ENR, BUSINESS)
    assert d.needs_review and not d.auto_send_reply
    assert any("prompt_injection_attempt" in r for r in d.review_reasons)


def test_low_confidence_and_high_value_go_to_review():
    assert decide(result(confidence=0.3), ENR, BUSINESS).needs_review
    big = decide(result(budget_usd=60000), ENR, BUSINESS)
    assert big.needs_review and "high-value" in big.review_reasons[0]


def test_non_sales_intents():
    assert decide(result(intent="spam", fit_score=0), ENR, BUSINESS).tier == "spam"
    support = decide(result(intent="support_request", fit_score=0), ENR, BUSINESS)
    assert support.tier == "support" and support.auto_send_reply and not support.create_deal
    assert decide(result(intent="vendor_pitch", fit_score=0), ENR, BUSINESS).tier == "other"
