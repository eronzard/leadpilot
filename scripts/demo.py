"""Replay the eval dataset through the full pipeline in-process and print the before/after metrics.

    python scripts/demo.py                 # rules qualifier, local CRM, outbox mailer
    python scripts/demo.py --chaos 0.3     # 30% of CRM writes fail with a 503 -> retries + checkpoints
    python scripts/demo.py --qualifier claude

Uses a throwaway SQLite file, so it never touches data/leadpilot.db.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from leadpilot.api import create_app  # noqa: E402
from leadpilot.app import build  # noqa: E402
from leadpilot.integrations.crm import FlakyCRM, LocalCRM  # noqa: E402
from leadpilot.metrics import compute  # noqa: E402
from leadpilot.settings import ROOT, get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qualifier", choices=["rules", "claude"], default="rules")
    parser.add_argument("--chaos", type=float, default=0.0, help="probability a CRM write fails transiently")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp()) / "demo.db"
    s = dataclasses.replace(get_settings(), qualifier=args.qualifier, db_path=str(tmp), crm="local",
                            mailer="outbox", slack_webhook_url="", retry_base_seconds=0.0, max_attempts=6,
                            fault_crm_rate=0.0, webhook_secret="", run_worker=False,
                            rate_limit_per_minute=0)
    ctx = build(s)
    if args.chaos:
        ctx.pipeline.crm = FlakyCRM(LocalCRM(ctx.db), args.chaos, rng=random.Random(args.seed))

    cases = [json.loads(l) for l in (ROOT / "evals" / "dataset.jsonl").read_text().splitlines() if l.strip()]
    with TestClient(create_app(ctx, start_worker=False)) as client:
        for case in cases:
            r = client.post("/webhooks/lead", json={**case["lead"], "source": "demo"})
            r.raise_for_status()
        # Re-deliver a few webhooks to prove idempotency.
        dupes = [client.post("/webhooks/lead", json={**c["lead"], "source": "demo"}).json()["duplicate"]
                 for c in cases[:5]]
        processed = ctx.worker.drain()

    m = compute(ctx.db, s.business)
    print(json.dumps(m, indent=2))
    print(f"\nqualifier={ctx.qualifier.name} chaos={args.chaos} job runs={processed} "
          f"duplicates ignored={sum(dupes)}/5 db={tmp}")


if __name__ == "__main__":
    main()
