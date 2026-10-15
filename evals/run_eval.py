"""Evaluate a qualifier + the routing rules against the labelled dataset.

    python -m evals.run_eval --qualifier rules
    python -m evals.run_eval --qualifier claude --model claude-opus-5-5 --effort low

Writes evals/results/<name>.json and .md. The dataset is synthetic (written by hand to cover
the failure modes this pipeline has to survive); it is not customer data.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from leadpilot.enrich import enrich, normalize
from leadpilot.models import LeadIn
from leadpilot.qualify import TransientQualifierError, make_qualifier
from leadpilot.routing import decide
from leadpilot.settings import ROOT, get_settings

DATASET = ROOT / "evals" / "dataset.jsonl"
HOLDOUT = ROOT / "evals" / "holdout.jsonl"
RESULTS = ROOT / "evals" / "results"


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_case(qualifier: Any, business: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    lead = normalize(LeadIn.model_validate(case["lead"]))
    enrichment = enrich(lead)
    started = time.perf_counter()
    for attempt in range(4):
        try:
            result = qualifier.qualify(lead, enrichment)
            break
        except TransientQualifierError:
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"{case['id']}: qualifier kept failing")
    latency = time.perf_counter() - started
    decision = decide(result, enrichment, business)
    exp = case["expected"]
    return {
        "id": case["id"], "tags": case["tags"],
        "expected_intent": exp["intent"], "got_intent": result.qualification.intent,
        "expected_tier": exp["tier"], "got_tier": decision.tier,
        "expected_review": exp.get("needs_review"), "got_review": decision.needs_review,
        "score": decision.score, "confidence": result.qualification.confidence,
        "review_reasons": decision.review_reasons, "red_flags": result.qualification.red_flags,
        "fallback_reason": result.fallback_reason,
        "latency_s": round(latency, 3), "cost_usd": result.cost_usd,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    intent_ok = sum(r["got_intent"] == r["expected_intent"] for r in rows)
    tier_ok = sum(r["got_tier"] == r["expected_tier"] for r in rows)
    hot_pred = [r for r in rows if r["got_tier"] == "hot"]
    hot_true = [r for r in rows if r["expected_tier"] == "hot"]
    tp = sum(r["expected_tier"] == "hot" for r in hot_pred)
    review_expected = [r for r in rows if r["expected_review"]]
    # Leads that reached a customer with no human check although they were not hot/warm/cold-correct.
    harmful = [r for r in rows if r["got_tier"] in {"hot", "warm", "cold", "support"}
               and r["expected_tier"] in {"spam", "other"} and not r["got_review"]]
    latencies = [r["latency_s"] for r in rows]
    tiers = sorted({r["expected_tier"] for r in rows} | {r["got_tier"] for r in rows})
    confusion = {t: Counter(r["got_tier"] for r in rows if r["expected_tier"] == t) for t in tiers}
    return {
        "cases": n,
        "intent_accuracy": round(intent_ok / n, 3),
        "tier_accuracy": round(tier_ok / n, 3),
        "hot_precision": round(tp / len(hot_pred), 3) if hot_pred else None,
        "hot_recall": round(tp / len(hot_true), 3) if hot_true else None,
        "review_recall": round(sum(r["got_review"] for r in review_expected) / len(review_expected), 3)
        if review_expected else None,
        "auto_replied_to_non_leads": len(harmful),
        "sent_to_review": sum(r["got_review"] for r in rows),
        "fallbacks": sum(1 for r in rows if r["fallback_reason"]),
        "latency_p50_s": round(statistics.median(latencies), 3),
        "latency_max_s": round(max(latencies), 3),
        "cost_usd_total": round(sum(r["cost_usd"] for r in rows), 4),
        "confusion_tier": {t: dict(c) for t, c in confusion.items()},
    }


def to_markdown(name: str, meta: dict[str, Any], summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [f"# Eval results: `{name}`", "",
             f"Run at {meta['run_at']} - dataset `{meta['dataset']}` - qualifier `{meta['qualifier']}`"
             + (f", model `{meta['model']}`, effort `{meta['effort']}`" if meta.get("model") else ""), "",
             "| metric | value |", "|---|---|"]
    for key in ["cases", "intent_accuracy", "tier_accuracy", "hot_precision", "hot_recall", "review_recall",
                "auto_replied_to_non_leads", "sent_to_review", "fallbacks", "latency_p50_s", "latency_max_s",
                "cost_usd_total"]:
        lines.append(f"| {key} | {summary[key]} |")
    tiers = list(summary["confusion_tier"])
    lines += ["", "## Tier confusion matrix (rows = expected, columns = predicted)", "",
              "| expected \\ got | " + " | ".join(tiers) + " |", "|---" * (len(tiers) + 1) + "|"]
    for t in tiers:
        lines.append(f"| **{t}** | " + " | ".join(str(summary["confusion_tier"][t].get(g, 0) or "")
                                                 for g in tiers) + " |")
    misses = [r for r in rows if r["got_tier"] != r["expected_tier"] or r["got_intent"] != r["expected_intent"]
              or (r["expected_review"] and not r["got_review"])]
    lines += ["", f"## Misses ({len(misses)})", "", "| case | expected | got | score | conf | notes |",
              "|---|---|---|---|---|---|"]
    for r in misses:
        notes = "; ".join(r["review_reasons"]) or ""
        if r["expected_review"] and not r["got_review"]:
            notes = "MISSED REVIEW. " + notes
        lines.append(f"| {r['id']} | {r['expected_intent']} / {r['expected_tier']} | "
                     f"{r['got_intent']} / {r['got_tier']} | {r['score']} | {r['confidence']} | {notes} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qualifier", choices=["rules", "claude"], default="rules")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None)
    parser.add_argument("--dataset", choices=["dev", "holdout"], default="dev",
                        help="dev = dataset.jsonl (used while tuning), holdout = holdout.jsonl (never tuned on)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--name", default=None, help="results file name (default: qualifier[-model-effort])")
    args = parser.parse_args()

    s = get_settings()
    kwargs: dict[str, Any] = {}
    if args.qualifier == "claude":
        if not s.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY is not set")
        kwargs = {"model": args.model or s.model, "effort": args.effort or s.effort, "api_key": s.anthropic_api_key}
    qualifier = make_qualifier(args.qualifier, s.business, **kwargs)
    cases = load_cases(DATASET if args.dataset == "dev" else HOLDOUT)
    if args.limit:
        cases = cases[: args.limit]

    workers = args.concurrency if args.qualifier == "claude" else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda c: run_case(qualifier, s.business, c), cases))

    summary = summarize(rows)
    name = args.name or ((args.qualifier if args.qualifier == "rules"
                          else f"claude-{kwargs['model']}-{kwargs['effort']}") + f"-{args.dataset}")
    meta = {"run_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "dataset": args.dataset, "qualifier": args.qualifier,
            "model": kwargs.get("model"), "effort": kwargs.get("effort")}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps({"meta": meta, "summary": summary, "cases": rows}, indent=2))
    (RESULTS / f"{name}.md").write_text(to_markdown(name, meta, summary, rows))
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {RESULTS / (name + '.md')}")


if __name__ == "__main__":
    main()
