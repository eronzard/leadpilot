# Deploying LeadPilot

LeadPilot is one process: the API and the worker thread share a SQLite file. Anything that runs a Docker container and keeps a persistent disk can host it.

## Hugging Face Space (free, public demo)

1. Create a new Space and pick the **Docker** SDK (blank template).
2. Push this repo to the Space, with this YAML block added at the top of the Space's `README.md`. Spaces read their config from it:

   ```yaml
   ---
   title: LeadPilot
   emoji: 🧭
   colorFrom: blue
   colorTo: indigo
   sdk: docker
   app_port: 7860
   pinned: false
   ---
   ```

3. Under **Settings → Variables and secrets**, optionally add:
   - `ANTHROPIC_API_KEY` (secret): turns on the Claude qualifier. Without it the Space runs the rules engine.
   - `LEADPILOT_ADMIN_TOKEN` (secret): protects the approve, dismiss and re-queue buttons on a public URL.
4. The Dockerfile already listens on port 7860 and runs as uid 1000, which is what Spaces expect. Without persistent storage, `/data` resets whenever the Space restarts, which is fine for a demo.

## Any Docker host (Render, Fly.io, Railway, a VPS)

```bash
docker build -t leadpilot .
docker run -d -p 7860:7860 -v leadpilot-data:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e LEADPILOT_WEBHOOK_SECRET=$(openssl rand -hex 32) \
  -e LEADPILOT_ADMIN_TOKEN=$(openssl rand -hex 16) \
  -e LEADPILOT_CRM=hubspot -e HUBSPOT_TOKEN=pat-... \
  -e SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... \
  leadpilot
```

## Production checklist

- [ ] `LEADPILOT_WEBHOOK_SECRET` is set, and every sender signs (see `n8n/` for a signing node)
- [ ] `LEADPILOT_ADMIN_TOKEN` is set if the dashboard is reachable from the internet
- [ ] `/data` is on a persistent volume, backed up (`sqlite3 leadpilot.db ".backup ..."`)
- [ ] An uptime check on `/healthz` alerts when `dead_letter > 0` or `queue_depth` keeps growing
- [ ] `LEADPILOT_MAILER=smtp` is enabled only after reviewing a week of drafts in outbox mode
- [ ] HubSpot private app scopes: `crm.objects.contacts.write`, `crm.objects.deals.write`, `crm.objects.deals.read`
