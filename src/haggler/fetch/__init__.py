"""Fetching: the Fetcher protocol and the RawResponse it returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from haggler.fetch.errors import (
    BlockedError,
    FetchError,
    RobotsDisallowedError,
    TransientFetchError,
)


@dataclass
class RawResponse:
    url: str
    final_url: str
    status_code: int
    text: str
    content_type: str = "text/html"
    fetched_at: datetime | None = None
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    # The raw bytes, kept only for a PDF (haggler.fetch.pdf.pdf_payload): application/pdf, or an
    # octet-stream / missing content type whose body starts with "%PDF-". b"" for everything
    # else, whose `text` (the decoded body, as always) is all there is.
    body: bytes = b""


class Fetcher(Protocol):
    async def fetch(
        self, url: str, *, accept: str = "text/html", egress: str = "default"
    ) -> RawResponse:
        """Return the response or raise a haggler.fetch.errors exception."""
        ...

    async def aclose(self) -> None: ...


__all__ = [
    "BlockedError",
    "FetchError",
    "Fetcher",
    "RawResponse",
    "RobotsDisallowedError",
    "TransientFetchError",
]
