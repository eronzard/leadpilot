# n8n intake workflow

[`leadpilot-intake.json`](leadpilot-intake.json) feeds LeadPilot from two sources:

```
Gmail trigger ─▶ map email ───┐
                              ├─▶ sign payload (HMAC) ─▶ POST /webhooks/lead ─▶ accepted? ─no─▶ Slack #ops-alerts
Typeform webhook ─▶ map form ─┘                         (5 tries, 3s apart)
```

n8n handles the connectors: Gmail OAuth, Typeform. LeadPilot handles the logic, state and retries. That way neither side duplicates the other's work, and the qualification logic has tests instead of living in a visual flow.

## Import

1. In n8n: **Workflows → Import from file** → `leadpilot-intake.json`.
2. Attach your Gmail credential to the trigger node and change the `q` filter to your inbox address.
3. Set these environment variables on the n8n instance:
   - `LEADPILOT_URL`: for example `https://your-space.hf.space`
   - `LEADPILOT_WEBHOOK_SECRET`: the same value LeadPilot uses
   - `SLACK_WEBHOOK_URL`: for rejection alerts
   - `NODE_FUNCTION_ALLOW_BUILTIN=crypto`: lets the Code node compute the HMAC
   - `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`: lets nodes read `$env`
4. Point Typeform's webhook at the production URL of the **Typeform webhook** node. The mapping looks for answers whose field `ref` is `name`, `company` and `message`.

Redeliveries are safe. If n8n retries a POST that actually succeeded, LeadPilot recognises the idempotency key and returns `{"duplicate": true}`.
