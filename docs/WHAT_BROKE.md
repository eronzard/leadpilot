# What broke (and what I changed)

This file only lists things that actually broke while I was building LeadPilot, and what each one taught me. Every number comes from a run that's checked into [`evals/results/`](../evals/results) or that you can reproduce with `scripts/demo.py`.

## 1. The first eval run: 79% tier accuracy, and three wrong calls that would have cost money

I built the rules qualifier before writing the dataset. Then I scored it against the 48 hand-labelled leads ([before](../evals/results/rules-dev-before-tuning.md)):

| | intent accuracy | tier accuracy |
|---|---|---|
| dev set, before tuning | 89.6% | 79.2% |
| holdout set, before tuning | 87.5% | 62.5% |

The misses fell into three groups:

- **Missing information was scored as bad information.** A sales inquiry started at 35/100, below the warm threshold of 45. So any lead that didn't mention a budget or timeline came out "cold" and got a polite "we may not be the best fit" email. Five of the nine warm leads in the dev set would have been brushed off. **Fix:** a lead now starts at 50, and only *negative* evidence moves it down: a budget below the minimum, a solo operator, a far-off timeline, or a request for free work.
- **The bare word "job" turned a $10k lead into a job applicant.** An HVAC owner wrote *"Want **job** requests from our site auto-created in ServiceTitan. $10k. Need it this month."* It was routed to "other", which means no CRM deal, no reply and no alert. **Fix:** job-seeker patterns are now phrases ("are you hiring", "looking for a position", "my resume"), not single words. There's a regression test for this case.
- **A phishing email was classified as a support request.** It mentioned a login, so the rules engine chose `support_request`, and support requests get an automatic acknowledgement. **Fix:** I added phishing patterns, a `possible_phishing` red flag that forces human review, and a regression test.

**Results after the fixes:**

| | intent accuracy | tier accuracy |
|---|---|---|
| dev set (tuned on, so optimistic) | 100% | 100% |
| **holdout set (written before tuning, never tuned on)** | **93.8%** | **93.8%** |

I wrote the holdout set *before* changing any rules, specifically so this comparison would mean something. The one remaining holdout miss (`h-cold-01`, "a bot that posts to my Twitter, can pay $50") goes to "other" instead of "cold". I've left it unfixed on purpose: tuning on the holdout would make it a second dev set. Both sets now run as a CI gate in [`tests/test_eval_regression.py`](../tests/test_eval_regression.py).

**What I took from it:** the rules engine is the fallback, not the product. The fact that 100% on dev turned into 94% on unseen data is the reason the primary path is an LLM. It's also why routing has hard guards that hold no matter which qualifier runs (see [ARCHITECTURE.md](ARCHITECTURE.md#guards-the-model-cannot-override)).

## 2. My own rate limiter blocked the demo replay

The intake webhook allows 30 unsigned requests per minute per IP. That limit protects a public form from abuse. When I replayed the 48-lead dataset through the API, request 31 came back as `429 Too Many Requests`.

Real traffic would hit the same wall: an n8n or Zapier workflow catching up on a backlog after an outage sends everything from one IP. **Fix:** requests carrying a valid HMAC signature come from a trusted integration, so they skip the per-IP limit. The limit is also configurable now (`LEADPILOT_RATE_LIMIT_PER_MINUTE`, 0 disables it). Two tests cover both behaviours.

## 3. A regex I "fixed" was broken by my own escaping

While adding the gibberish detector (`asdf…`, `test test 123`), I wrote the pattern through a script that doubled the backslashes. That left `\\btest test\\b` inside a raw string, which matches a literal backslash and can never fire. The eval didn't catch it, because another branch of the same pattern (`asdf`) happened to cover the dataset case. I spotted it reading the diff and added a regression test that uses the case the eval missed.

**What I took from it:** a passing eval tells you the cases it contains pass. It doesn't tell you every branch of your code works.

## 4. Retries are not a guarantee, and the demo proves it

`python scripts/demo.py --chaos 0.3` makes 30% of CRM writes fail with a fake 503. Over 48 leads that produced 28 retries. Every retry resumed from its checkpoint: no second qualifier call, no duplicate CRM deal, no second Slack ping.

**One lead still failed six attempts in a row and was dead-lettered.** That's the expected outcome. Each attempt makes up to two CRM writes, so the chance of failing six times running is about 0.51⁶ ≈ 1.8% per lead. The system didn't drop it: it moved the lead to the dead-letter queue, posted to `#ops-alerts`, and the dashboard offers a one-click **Re-queue** once the CRM recovers. `test_exhausted_retries_dead_letter_and_alert` covers that path end to end.

## Known limitations (not fixed yet)

- **The Claude path has not run against the live API from this repo yet.** I built it without an API key available. Its request shape, response parsing and failure handling (refusal, rate limit, connection error, 4xx, unparseable output) are covered by tests against a fake client ([`tests/test_claude_qualifier.py`](../tests/test_claude_qualifier.py)). To get real numbers, run `python -m evals.run_eval --qualifier claude --dataset holdout` with a key set.
- **The eval data is synthetic.** I wrote the 64 leads to cover known failure modes: injection, phishing, vendor pitches that sound like sales, non-English leads, missing details. They are not real customer traffic. On a real deployment, the first step would be labelling a sample of the actual inbox.
- **HubSpot deal deduplication is eventually consistent.** Before creating a deal, the adapter searches for one tagged `[LP-<lead id>]`. HubSpot's search index lags a few seconds, so a retry fired within that window after a *lost* response could still create a duplicate. A custom `leadpilot_lead_id` property with a unique value would close the gap.
- **"First response time" only counts replies that were actually sent.** Hot-lead drafts waiting for approval aren't in the median until someone approves them. The dashboard labels the metric accordingly.
- **The "before" numbers are a stated baseline, not a measurement.** The 15 minutes per lead and 9-hour first response come from [`config/business.yaml`](../config/business.yaml). They describe the manual process for the fictional demo business. Every "after" number is measured.
