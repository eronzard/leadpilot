# LeadPilot

**Inbound lead automation, end to end.** A lead arrives from a web form, Gmail or Typeform. LeadPilot enriches it, has Claude qualify it with structured output, scores and routes it with deterministic rules, and syncs it to HubSpot. It alerts Slack and replies within seconds. Retries resume from checkpoints, a dead-letter queue catches what still fails, and a review queue lets a person approve anything risky before it goes out.

[![CI](https://github.com/eronzard/leadpilot/actions/workflows/ci.yml/badge.svg)](https://github.com/eronzard/leadpilot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

![LeadPilot dashboard](docs/img/dashboard.png)

## The process it replaces

A small services business (the demo uses a fictional automation consultancy) used to handle inbound inquiries by hand. Someone read the shared inbox twice a day, googled the company, typed the lead into the CRM, decided whether it was worth a call, and wrote a reply.

| | Before (manual) | After (LeadPilot) |
|---|---|---|
| Team time per lead | 15 min: triage 3, research 4, CRM entry 3, reply 5 | **0.94 min**: only flagged leads get a 2-minute human review |
| Team time for 47 leads | 11.75 h | **0.7 h** (11 h saved) |
| Median time to first reply | ~9 h (inbox batched twice a day) | **seconds** for auto-replied tiers; hot leads wait for a one-click approval |
| Leads handled with zero human touch | 0% | **53%** |
| CRM data entry | retyped by hand | upsert by email, deal created with amount and services |
| Prompt-injection, phishing or spam reaching a customer reply | relied on whoever read it | **0 in eval**: all forced into review or dropped |

> **How these numbers were produced.** "Before" is the documented baseline for the demo business in [`config/business.yaml`](config/business.yaml). It's a stated assumption, not a measurement. "After" is measured by `python scripts/demo.py --chaos 0.3`, which replays 48 labelled leads through the real API, worker and database while **30% of CRM writes fail on purpose**. That run produced 28 retries and 0 duplicate deals. One lead was dead-lettered and alerted, and can be re-queued from the dashboard. Every metric is recomputed live in the dashboard from the database.

## How it works

```mermaid
flowchart LR
    S["Web form · Gmail · Typeform<br/>(n8n / Zapier / direct)"] -->|"POST /webhooks/lead<br/>HMAC-signed, idempotent"| Q[(job queue)]
    Q --> E[enrich] --> C["qualify<br/>Claude + JSON schema"] --> D["decide<br/>plain-code rules"]
    D --> CRM[HubSpot contact + deal] --> R["reply<br/>template + 1 personal line"] --> N[Slack alert]
    D -->|"risky / unsure / high value"| H[human review queue]
    C -.->|"refusal · 4xx · bad output"| F[rules fallback + forced review]
```

1. **Intake.** `POST /webhooks/lead` takes JSON or form posts. It checks an HMAC signature, rate-limits unsigned traffic, and de-duplicates redeliveries with an idempotency key. The lead and its job are written in one transaction.
2. **Enrich.** Free and disposable email detection, company domain, prior leads from the same contact, and optionally the website's title and description (with SSRF protection).
3. **Qualify.** One Claude call per lead (`claude-opus-5-5`, effort `low`). Structured outputs constrain the response to a JSON schema covering intent, budget, timeline, size, industry, fit score, confidence and red flags. The ICP rubric is a cached system prompt, and the lead is passed as untrusted data inside `<lead>` tags. Server-side fallback covers safety-classifier false positives.
4. **Decide.** Plain code, not the model, picks the tier (hot, warm, cold, support, other, spam) and the review gate. A disposable email, a budget below minimum, a "later" timeline or an injection attempt can't be talked into "hot".
5. **Act.** Upsert the CRM contact, create a deal for hot and warm leads, draft a reply from approved EN/ES/FR templates (the model writes only one personal sentence), and send it or hold it for approval. Then notify Slack.
6. **Recover.** Every step is checkpointed. A retry after a CRM 503 skips the LLM call it already paid for. Transient errors back off exponentially; permanent ones (bad token) go straight to the dead-letter queue with an `#ops-alerts` message.

The full design is in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

![Review queue: a prompt-injection attempt held for a human](docs/img/review-injection.png)

## What broke

The short version of [**docs/WHAT_BROKE.md**](docs/WHAT_BROKE.md):

- **My first eval run scored 79% on lead tier.** Missing details were treated as negative signals, so warm leads got "not a fit" emails. "Want **job** requests auto-created" from an HVAC owner was read as a job application, and a phishing email was read as a support request, which would have triggered an auto-reply. After fixing those: 100% on the dev set I tuned on, and **93.8% on a holdout set I wrote before tuning and never tuned on.**
- **My own rate limiter blocked a backlog replay.** A real n8n catch-up after an outage would have hit the same limit. Signed integrations now bypass the per-IP limit.
- **Retries don't guarantee delivery.** At a 30% fault rate, one lead in 48 failed six times in a row. The design answer is a dead-letter queue, an alert and a one-click re-queue, not more retries.
- **Known gap:** the Claude path is tested against a fake client (shape, parsing, refusal, 429, 4xx, garbage output) but hasn't run against the live API from this repo yet. One command gives you real numbers: `python -m evals.run_eval --qualifier claude --dataset holdout`.

## Evals

64 hand-written leads ([`evals/dataset.jsonl`](evals/dataset.jsonl) for dev, [`evals/holdout.jsonl`](evals/holdout.jsonl) for holdout). They target the cases that break lead pipelines: vendor pitches that sound like buyers, job seekers who mention automation, phishing, keyboard-mash spam, prompt injection, Spanish and French leads, and vague inquiries with no budget.

| Qualifier | Set | Intent acc. | Tier acc. | Hot precision / recall | Review recall* | Non-leads auto-replied |
|---|---|---|---|---|---|---|
| rules, before tuning | dev | 89.6% | 79.2% | 1.00 / 0.93 | 1.00 | 1 |
| rules, before tuning | holdout | 87.5% | 62.5% | 1.00 / 1.00 | 1.00 | 0 |
| rules, after tuning | dev (tuned on) | 100% | 100% | 1.00 / 1.00 | 1.00 | 0 |
| **rules, after tuning** | **holdout** | **93.8%** | **93.8%** | **1.00 / 1.00** | **1.00** | **0** |
| Claude (`claude-opus-5-5`, low) | both | run it ↓ | | | | |

\* Share of leads that *must* reach a human (injection, high value, partnership) that actually did.

```bash
python -m evals.run_eval --qualifier rules --dataset holdout
ANTHROPIC_API_KEY=... python -m evals.run_eval --qualifier claude --dataset holdout --effort low
```

Each run writes a report with a confusion matrix and every miss to [`evals/results/`](evals/results). The rules results also run as a CI gate ([`tests/test_eval_regression.py`](tests/test_eval_regression.py)), so a change that lowers accuracy fails the build.

## Quickstart

```bash
git clone https://github.com/eronzard/leadpilot && cd leadpilot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add ANTHROPIC_API_KEY to use Claude; without it the rules engine runs
python -m leadpilot             # http://localhost:8000 (dashboard) · /docs (OpenAPI)
```

Then click **+ Send test lead → Load 12 sample leads**, or run:

```bash
python scripts/seed.py http://localhost:8000           # post all sample leads via the webhook
python scripts/demo.py --chaos 0.3                     # in-process replay with injected CRM failures
pytest                                                 # 48 tests: unit, API, worker, failure paths, eval gate
docker build -t leadpilot . && docker run -p 7860:7860 leadpilot
```

Send a lead the way any form or integration would:

```bash
curl -X POST localhost:8000/webhooks/lead -H 'content-type: application/json' -d '{
  "name": "Dr. Priya Lee", "email": "priya@brightsmile-dental.example", "company": "BrightSmile Dental",
  "message": "Owner of a 40-person dental practice. Front desk re-types 15h/week of appointment requests. Budget $8,000, want it live in 3 weeks."
}'
# {"lead_id": "…", "status": "queued", "duplicate": false}
```

## Reliability: what happens when things fail

| Failure | What LeadPilot does |
|---|---|
| Form double-submit or webhook redelivery | Same idempotency key, so it returns the original lead id. Processed once. |
| CRM or Slack returns 429 or 5xx, or a timeout | Exponential backoff with jitter. Resumes from the last checkpoint, no duplicate writes. |
| Bad CRM token or validation error | Permanent: dead-lettered immediately with an alert. Retrying wouldn't help. |
| Retries exhausted | Dead-letter queue, a `#ops-alerts` Slack message, and a one-click re-queue in the dashboard. |
| Worker crashes mid-job | Lease expiry makes the job claimable again. |
| Claude rate-limited or unreachable | Job retried with backoff. |
| Claude declines, returns 4xx or unparseable output | Server-side model fallback first, then the rules engine with forced human review. |
| Lead contains "ignore previous instructions…" | Detected by Claude and by a regex. Score can't be raised by the text, auto-send blocked, sent to review. |
| Huge or abusive form traffic | 64 KB body cap, per-IP rate limit for unsigned calls, signature check for integrations. |

## How this maps to the role

| The brief asks for | Where it is |
|---|---|
| "A process you automated end to end, with the before and after" | [The table above](#the-process-it-replaces), live in the dashboard and `/api/metrics` |
| Process analysis and design | The manual baseline and ICP in [`config/business.yaml`](config/business.yaml); the step design in [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Integrations between platforms, APIs and AI tools | HubSpot v3 ([`integrations/crm.py`](leadpilot/integrations/crm.py)), Slack, SMTP, Claude, and an importable [n8n workflow](n8n/) with Gmail and Typeform triggers |
| AI-driven systems | Claude structured-output qualifier with a rules fallback ([`qualify.py`](leadpilot/qualify.py)) |
| Debugging API integrations and prompt logic | Typed transient/permanent errors, an audit log per lead, an eval suite with a confusion matrix and every miss listed |
| Monitoring, testing, optimizing for reliability | 48 tests, CI on 3 Python versions plus a Docker smoke test, chaos mode, `/healthz`, dead-letter alerts |
| Clear, documented architecture | [ARCHITECTURE.md](docs/ARCHITECTURE.md), [DEPLOY.md](docs/DEPLOY.md), [WHAT_BROKE.md](docs/WHAT_BROKE.md) |

## Project layout

```
leadpilot/
  api.py            webhook, review API, dashboard routes
  pipeline.py       checkpointed steps + human approve/dismiss
  worker.py         queue consumer: backoff, dead letter, alerts
  qualify.py        ClaudeQualifier (structured outputs) + RulesQualifier
  routing.py        deterministic tiering and review gate
  replies.py        EN/ES/FR reply templates
  enrich.py         normalisation, idempotency keys, enrichment
  metrics.py        before/after computation
  db.py             SQLite schema: leads, jobs, steps, events, CRM, outbox
  integrations/     HubSpot + local CRM, Slack, SMTP/outbox, chaos wrapper
  static/           dashboard (vanilla JS, no build step)
config/business.yaml  ICP, routing thresholds, manual-process baseline
evals/              datasets, runner, checked-in results
n8n/                importable intake workflow
scripts/            demo.py (in-process replay), seed.py (post to a server)
tests/              pytest suite
```

## Configuration

Everything is set through environment variables; see [`.env.example`](.env.example). The main switches:

| Variable | Default | |
|---|---|---|
| `LEADPILOT_QUALIFIER` | `claude` | Falls back to `rules` when there's no `ANTHROPIC_API_KEY` |
| `LEADPILOT_MODEL` / `LEADPILOT_EFFORT` | `claude-opus-5-5` / `low` | |
| `LEADPILOT_CRM` | `local` | `hubspot` with `HUBSPOT_TOKEN` |
| `LEADPILOT_MAILER` | `outbox` | `smtp` actually sends; start with outbox and review the drafts |
| `LEADPILOT_WEBHOOK_SECRET` | none | Requires HMAC-signed webhooks |
| `LEADPILOT_ADMIN_TOKEN` | none | Protects approve, dismiss and re-queue on a public deployment |
| `LEADPILOT_FAULT_CRM_RATE` | `0` | Chaos testing: probability that a CRM write fails |

To point it at a different business (a dental group, a law firm, a Shopify store), edit [`config/business.yaml`](config/business.yaml). The prompt, routing and replies all read from it.

## Next steps

- Run the Claude evals on both sets and compare with the rules engine; sweep effort `low`/`medium`.
- Label 100 real inbox messages from a pilot business and make that the holdout.
- Use a unique `leadpilot_lead_id` HubSpot property instead of tag search for deal deduplication.
- Postgres plus `SKIP LOCKED` for multiple workers. The queue interface already isolates this to `claim_job()`.

## License

MIT
