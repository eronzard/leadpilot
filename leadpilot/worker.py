"""Background worker: claims jobs, runs the pipeline, retries with backoff, dead-letters."""

from __future__ import annotations

import logging
import random
import threading
from typing import Optional

from .db import Database
from .integrations import PermanentIntegrationError, TransientIntegrationError
from .integrations.notify import Notifier
from .pipeline import Pipeline
from .qualify import TransientQualifierError

log = logging.getLogger("leadpilot.worker")

RETRYABLE = (TransientIntegrationError, TransientQualifierError)


class Worker:
    def __init__(self, db: Database, pipeline: Pipeline, notifier: Notifier,
                 max_attempts: int = 4, retry_base_seconds: float = 5.0, poll_seconds: float = 0.5):
        self.db = db
        self.pipeline = pipeline
        self.notifier = notifier
        self.max_attempts = max_attempts
        self.retry_base = retry_base_seconds
        self.poll = poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter: base * 2^(n-1) * U(0.5, 1.0)."""
        return self.retry_base * (2 ** (attempt - 1)) * random.uniform(0.5, 1.0)

    def run_once(self) -> bool:
        """Process one ready job. Returns False when the queue had nothing ready."""
        job = self.db.claim_job()
        if job is None:
            return False
        lead_id, attempt = job["lead_id"], job["attempts"]
        try:
            self.pipeline.process(lead_id, attempt=attempt)
        except PermanentIntegrationError as exc:
            self._dead_letter(job, f"permanent: {exc}")
        except Exception as exc:  # noqa: BLE001 - unknown errors are retried, then dead-lettered
            kind = "transient" if isinstance(exc, RETRYABLE) else "unexpected"
            error = f"{kind}: {type(exc).__name__}: {exc}"
            if attempt >= self.max_attempts:
                self._dead_letter(job, error)
            else:
                delay = self.backoff(attempt)
                self.db.retry_job(job["id"], delay, error)
                self.db.update_lead(lead_id, status="queued", error=error)
                self.db.log(lead_id, "job.retry", f"attempt {attempt} failed ({error}); retry in {delay:.1f}s",
                            level="warn")
                if kind == "unexpected":
                    log.exception("unexpected error processing lead %s", lead_id)
        else:
            self.db.finish_job(job["id"])
        return True

    def _dead_letter(self, job: dict, error: str) -> None:
        lead_id = job["lead_id"]
        self.db.dead_letter_job(job["id"], error)
        self.db.update_lead(lead_id, status="dead", error=error)
        self.db.log(lead_id, "job.dead_letter", f"gave up after {job['attempts']} attempt(s): {error}", level="error")
        try:
            self.notifier.send(lead_id, "#ops-alerts",
                               f"Lead {lead_id} moved to dead letter after {job['attempts']} attempt(s): {error}")
        except Exception:  # noqa: BLE001 - alerting must never crash the worker
            log.exception("failed to send dead-letter alert")

    def drain(self, max_jobs: int = 10_000) -> int:
        """Process everything that is ready now (used by tests, evals and the demo script)."""
        n = 0
        while n < max_jobs and self.run_once():
            n += 1
        return n

    # ------------------------------------------------------------------ thread lifecycle
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                busy = False
            if not busy:
                self._stop.wait(self.poll)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="leadpilot-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
