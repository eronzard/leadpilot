"""Before/after metrics.

"After" numbers are measured from the database. "Before" numbers come from the baseline in
config/business.yaml, which documents the manual process being replaced; those are stated
assumptions, and the API response labels them as such.
"""

from __future__ import annotations

import statistics
from typing import Any, Optional

from .db import Database


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def compute(db: Database, business: dict[str, Any]) -> dict[str, Any]:
    leads = db.query("SELECT id, status, tier, received_at, completed_at, first_response_at, "
                     "human_touches, needs_review, cost_usd, reply_status FROM leads")
    jobs = db.query("SELECT attempts, status FROM jobs")
    total = len(leads)
    completed = [l for l in leads if l["completed_at"]]
    by_tier: dict[str, int] = {}
    for l in leads:
        if l["tier"]:
            by_tier[l["tier"]] = by_tier.get(l["tier"], 0) + 1

    processing_s = [l["completed_at"] - l["received_at"] for l in completed]
    response_min = [(l["first_response_at"] - l["received_at"]) / 60 for l in leads if l["first_response_at"]]
    pending_review = sum(1 for l in leads if l["status"] == "needs_review")
    reviewed = sum(l["human_touches"] for l in leads)
    zero_touch = sum(1 for l in completed if l["status"] == "done" and l["human_touches"] == 0)

    base = business["baseline_manual_process"]
    manual_minutes_per_lead = sum(base["minutes_per_lead"].values())
    before_minutes = manual_minutes_per_lead * len(completed)
    # Every lead that needs (or needed) a person costs one review, whether or not it is done yet.
    human_reviews = reviewed + pending_review
    after_minutes = human_reviews * base["minutes_per_human_review"]

    llm_cost = sum(l["cost_usd"] or 0 for l in leads)
    return {
        "leads_total": total,
        "leads_completed": len(completed),
        "by_tier": by_tier,
        "pending_review": pending_review,
        "dead_letter": sum(1 for j in jobs if j["status"] == "dead"),
        "retries": sum(max(0, j["attempts"] - 1) for j in jobs),
        "zero_touch_rate": round(zero_touch / len(completed), 3) if completed else None,
        "processing_seconds_median": round(statistics.median(processing_s), 2) if processing_s else None,
        "processing_seconds_p95": round(_percentile(processing_s, 0.95), 2) if processing_s else None,
        "first_response_minutes_median": round(statistics.median(response_min), 2) if response_min else None,
        "llm_cost_usd_total": round(llm_cost, 4),
        "llm_cost_usd_per_lead": round(llm_cost / len(completed), 5) if completed else None,
        "before_after": {
            "manual_minutes_per_lead": {"before": manual_minutes_per_lead,
                                        "after": round(after_minutes / len(completed), 2) if completed else None},
            "team_minutes_for_these_leads": {"before": before_minutes, "after": after_minutes},
            "hours_saved": round((before_minutes - after_minutes) / 60, 1),
            "first_response_minutes_median": {
                "before": base["median_first_response_minutes"],
                "after": round(statistics.median(response_min), 2) if response_min else None,
            },
            "note": "'before' values are the documented manual-process baseline in "
                    "config/business.yaml (assumptions); 'after' values are measured.",
        },
    }
