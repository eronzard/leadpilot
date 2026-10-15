"""Quality gate: CI fails if the rules engine regresses on either eval set."""

from evals.run_eval import DATASET, HOLDOUT, load_cases, run_case, summarize
from leadpilot.qualify import RulesQualifier

from .conftest import BUSINESS


def run(path):
    q = RulesQualifier(BUSINESS)
    return summarize([run_case(q, BUSINESS, c) for c in load_cases(path)])


def test_dev_set():
    s = run(DATASET)
    assert s["tier_accuracy"] >= 0.95
    assert s["auto_replied_to_non_leads"] == 0
    assert s["review_recall"] == 1.0


def test_holdout_set():
    # Never tuned on. A lower bar than dev on purpose: it measures generalisation.
    s = run(HOLDOUT)
    assert s["tier_accuracy"] >= 0.85
    assert s["auto_replied_to_non_leads"] == 0
    assert s["review_recall"] == 1.0
