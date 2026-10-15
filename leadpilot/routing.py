"""Deterministic routing: facts in, decision out. No model output is trusted blindly here."""

from __future__ import annotations

from typing import Any

from .models import Decision, Enrichment, QualifierResult

# Red flags that always pull a lead into the human review queue.
REVIEW_FLAGS = {"prompt_injection_attempt", "possible_phishing", "llm_unavailable"}


def decide(result: QualifierResult, enrichment: Enrichment, business: dict[str, Any]) -> Decision:
    q = result.qualification
    rules = business["routing"]
    icp = business["ideal_customer"]

    score = q.fit_score
    # Hard guards that hold regardless of what the qualifier said.
    if enrichment.is_disposable_email:
        score = min(score, 20)
    if q.budget_usd is not None and q.budget_usd < icp["min_budget_usd"]:
        score = min(score, rules["hot_threshold"] - 1)
    if q.timeline == "later":  # "hot" means they want to start soon
        score = min(score, rules["hot_threshold"] - 1)

    if q.intent == "spam":
        tier = "spam"
    elif q.intent == "support_request":
        tier = "support"
    elif q.intent in {"job_application", "vendor_pitch", "partnership", "other"}:
        tier = "other"
    elif score >= rules["hot_threshold"]:
        tier = "hot"
    elif score >= rules["warm_threshold"]:
        tier = "warm"
    else:
        tier = "cold"

    review: list[str] = []
    if q.confidence < rules["review_confidence_below"] and tier != "spam":
        review.append(f"low confidence ({q.confidence:.2f})")
    flagged = sorted(REVIEW_FLAGS & set(q.red_flags))
    if flagged:
        review.append("red flags: " + ", ".join(flagged))
    if q.budget_usd is not None and q.budget_usd >= rules["review_budget_at_or_above"]:
        review.append(f"high-value deal (${q.budget_usd:,})")
    if q.intent == "partnership":
        review.append("partnership request needs an owner decision")

    return Decision(
        tier=tier,
        score=score,
        needs_review=bool(review),
        review_reasons=review,
        create_deal=tier in {"hot", "warm"},
        notify_channel={"hot": "#sales-hot", "support": "#support"}.get(tier,
                        "#lead-review" if review else None),
        auto_send_reply=(tier in rules["auto_send_tiers"]) and not review,
    )
