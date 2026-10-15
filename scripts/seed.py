"""Post the sample leads to a running LeadPilot instance, the way a form or n8n would.

    python scripts/seed.py http://localhost:8000
    LEADPILOT_WEBHOOK_SECRET=... python scripts/seed.py https://your-space.hf.space --limit 10
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default="http://localhost:8000")
    parser.add_argument("--dataset", default=str(ROOT / "evals" / "dataset.jsonl"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    secret = os.environ.get("LEADPILOT_WEBHOOK_SECRET", "")
    cases = [json.loads(l) for l in Path(args.dataset).read_text().splitlines() if l.strip()]
    if args.limit:
        cases = cases[: args.limit]
    with httpx.Client(base_url=args.url, timeout=15) as client:
        for case in cases:
            body = json.dumps({**case["lead"], "source": "seed"}).encode()
            headers = {"content-type": "application/json"}
            if secret:
                headers["X-LeadPilot-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            r = client.post("/webhooks/lead", content=body, headers=headers)
            print(f"{case['id']:<12} {r.status_code} {r.text[:80]}")


if __name__ == "__main__":
    main()
