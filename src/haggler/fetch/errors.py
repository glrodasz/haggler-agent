"""Fetch-layer exceptions. Only TransientFetchError is retried by the Workflow."""


class FetchError(Exception):
    """Permanent failure (4xx other than 429, malformed response...)."""

    def __init__(self, message: str, *, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status = status
        self.url = url


class TransientFetchError(FetchError):
    """Timeouts, connection resets, 429 and 5xx: worth retrying."""


class BlockedError(FetchError):
    """Bot protection / CAPTCHA / 403 challenge page. Never retried, never evaded."""


class RobotsDisallowedError(FetchError):
    """robots.txt disallows our user agent for this URL."""
