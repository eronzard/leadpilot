"""Side-effecting integrations. Every write is idempotent so a retried job never double-posts."""


class TransientIntegrationError(Exception):
    """Worth retrying: rate limits, 5xx, timeouts."""


class PermanentIntegrationError(Exception):
    """Retrying will not help: bad credentials, validation errors. Goes straight to dead letter."""
