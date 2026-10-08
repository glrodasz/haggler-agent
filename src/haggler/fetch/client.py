"""HttpFetcher: the production Fetcher, with the project's crawling etiquette built in.

WHY a hand-rolled fetcher (instead of ADK's load_web_page or plain httpx defaults):
- we need the raw page (load_web_page drops short lines such as "129 kr");
- every request is identifiable, rate limited per host and checked against robots.txt,
  including redirect hops and the robots.txt download itself;
- failures are classified so the Workflow retries only what is worth retrying
  (TransientFetchError) and never retries or evades a bot wall (BlockedError).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpx

from haggler.config import Settings
from haggler.fetch import RawResponse
from haggler.fetch.errors import (
    BlockedError,
    FetchError,
    RobotsDisallowedError,
    TransientFetchError,
)
from haggler.fetch.robots import (
    ROBOTS_MAX_BYTES,
    RobotsCache,
    RobotsFetchResult,
    RobotsRules,
)

log = logging.getLogger(__name__)

MAX_REDIRECTS = 5
MAX_BODY_BYTES = 10 * 1024 * 1024
# A robots.txt Crawl-delay above our own interval is honoured, up to this cap.
MAX_CRAWL_DELAY_S = 30.0
ACCEPT_LANGUAGE = "en-US,en;q=0.8"
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
# Headers we never keep on RawResponse (they end up in logs and stored task records).
_DROP_HEADERS = frozenset({"set-cookie"})
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.I)

Monotonic = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


# ------------------------------------------------------------------ bot walls

# (vendor, markers) — matched against the lower-cased, entity-unescaped start of the body.
_BODY_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "cloudflare",
        (
            "cf-chl",
            "/cdn-cgi/challenge-platform/",
            "challenges.cloudflare.com",
            "just a moment...",
            "attention required! | cloudflare",
            "cf-error-details",
        ),
    ),
    ("perimeterx", ("px-captcha", "_pxappid", "perimeterx", "px-cloud.net", "press & hold")),
    ("datadome", ("captcha-delivery.com", "datadome")),
    ("imperva", ("incapsula incident id", "_incapsula_resource")),
    ("aws_waf", ("aws-waf-token", "awswafintegration")),
    (
        "captcha",
        (
            "captcha",
            "verify you are human",
            "are you a robot",
            "unusual traffic from your computer",
        ),
    ),
)


def looks_like_bot_wall(headers: httpx.Headers | dict[str, str], text: str) -> str | None:
    """Return the bot-protection vendor whose challenge/deny page this is, or None.

    Public so adapters/pipeline can apply the same test to 2xx pages (some walls answer 200).
    """
    h = {k.lower(): v.lower() for k, v in headers.items()}
    if h.get("cf-mitigated") == "challenge":
        return "cloudflare"
    if "x-datadome" in h or "x-dd-b" in h:
        return "datadome"
    if h.get("x-amzn-waf-action") in {"captcha", "challenge", "block"}:
        return "aws_waf"
    body = html.unescape(text[:65536]).lower()
    for vendor, markers in _BODY_MARKERS:
        if any(m in body for m in markers):
            return vendor
    # Akamai's deny page: <TITLE>Access Denied</TITLE> ... Reference&#32;&#35;18&#46;...
    if "access denied" in body and (
        "reference #" in body or "edgesuite.net" in body or "akamai" in h.get("server", "")
    ):
        return "akamai"
    return None


# ------------------------------------------------------------------ helpers


@dataclass
class _Hop:
    url: str
    status: int
    headers: httpx.Headers
    body: bytes = b""
    encoding: str | None = None
    ms: int = 0
    truncated: bool = False


def _accept_header(accept: str) -> str:
    return HTML_ACCEPT if accept.strip() == "text/html" else accept


def _check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FetchError(f"unsupported URL: {url!r}", url=url)
    return parts.hostname.lower()


def _decode(body: bytes, encoding: str | None) -> str:
    candidates = [encoding] if encoding else []
    if not encoding:
        m = _META_CHARSET.search(body[:4096])
        if m:
            candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for enc in candidates:
        try:
            return body.decode(enc, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def _mime(headers: httpx.Headers, fallback: str) -> str:
    value = headers.get("content-type", "")
    mime = value.split(";", 1)[0].strip().lower()
    return mime or fallback.split(",", 1)[0].strip()


def _retry_after(headers: httpx.Headers) -> str:
    value = headers.get("retry-after")
    return f" (retry-after {value})" if value else ""


def classify_status(url: str, status: int, headers: httpx.Headers, text: str) -> None:
    """Raise the haggler.fetch.errors exception matching a non-2xx final response."""
    if 200 <= status < 300:
        return
    if status == 429:
        raise TransientFetchError(
            f"HTTP 429 Too Many Requests{_retry_after(headers)}", status=status, url=url
        )
    if (status == 503 or (400 <= status < 500 and status not in {404, 410})) and (
        vendor := looks_like_bot_wall(headers, text)
    ):
        raise BlockedError(f"bot wall ({vendor}) HTTP {status}", status=status, url=url)
    if status >= 500:
        raise TransientFetchError(f"HTTP {status}{_retry_after(headers)}", status=status, url=url)
    if status in {404, 410}:
        raise FetchError(f"HTTP {status} not found", status=status, url=url)
    if status >= 400:
        raise FetchError(f"HTTP {status}", status=status, url=url)
    raise FetchError(f"unexpected HTTP {status}", status=status, url=url)


# ------------------------------------------------------------------ fetcher


class HttpFetcher:
    """Fetcher protocol implementation over httpx (see haggler.fetch.Fetcher)."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        monotonic: Monotonic = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        self._monotonic = monotonic
        self._sleep = sleep
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_start: dict[str, float] = {}
        self._crawl_delay: dict[str, float] = {}
        self._robots = RobotsCache(self._download_robots)

    async def __aenter__(self) -> HttpFetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---- public API

    async def fetch(
        self, url: str, *, accept: str = "text/html", egress: str = "default"
    ) -> RawResponse:
        if egress != "default":
            raise FetchError("egress not supported yet", url=url)
        accept_header = _accept_header(accept)
        current = url
        total_ms = 0
        for _ in range(MAX_REDIRECTS + 1):
            _check_url(current)
            await self._check_robots(current)
            hop = await self._get(current, accept=accept_header, limit=MAX_BODY_BYTES)
            total_ms += hop.ms
            if hop.status in REDIRECT_CODES:
                location = hop.headers.get("location")
                if not location:
                    raise FetchError(
                        f"HTTP {hop.status} without Location", status=hop.status, url=url
                    )
                current = urljoin(current, location.strip())
                continue
            text = _decode(hop.body, hop.encoding)
            classify_status(url, hop.status, hop.headers, text)
            return RawResponse(
                url=url,
                final_url=current,
                status_code=hop.status,
                text=text,
                content_type=_mime(hop.headers, accept),
                fetched_at=datetime.now(UTC),
                headers={
                    k.lower(): v for k, v in hop.headers.items() if k.lower() not in _DROP_HEADERS
                },
                elapsed_ms=total_ms,
            )
        raise FetchError(f"more than {MAX_REDIRECTS} redirects", url=url)

    # ---- politeness

    def _interval(self, host: str) -> float:
        delay = min(self._crawl_delay.get(host, 0.0), MAX_CRAWL_DELAY_S)
        return max(self.settings.per_host_interval_s, delay)

    @asynccontextmanager
    async def _host_slot(self, host: str) -> AsyncIterator[None]:
        """One request in flight per host, and >= interval seconds between request starts."""
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last_start.get(host)
            if last is not None:
                wait = last + self._interval(host) - self._monotonic()
                if wait > 0:
                    await self._sleep(wait)
            self._last_start[host] = self._monotonic()
            yield

    # ---- robots.txt

    async def _check_robots(self, url: str) -> None:
        if not self.settings.respect_robots:
            return
        rules: RobotsRules = await self._robots.rules_for(url)
        host = urlsplit(url).hostname or ""
        delay = rules.crawl_delay(self.settings.user_agent)
        if delay:
            self._crawl_delay[host.lower()] = delay
        if not rules.can_fetch(self.settings.user_agent, url):
            raise RobotsDisallowedError(f"robots.txt disallows this URL ({rules.reason})", url=url)

    async def _download_robots(self, url: str) -> RobotsFetchResult:
        """GET robots.txt, following up to 5 redirects (RFC 9309 §2.3.1.2), politely."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            hop = await self._get(
                current, accept="text/plain,*/*;q=0.5", limit=ROBOTS_MAX_BYTES, truncate=True
            )
            location = hop.headers.get("location")
            if hop.status not in REDIRECT_CODES or not location:
                return RobotsFetchResult(status=hop.status, text=_decode(hop.body, hop.encoding))
            current = urljoin(current, location.strip())
        # Still redirecting after 5 hops: report the 3xx, which rules_from_response treats as
        # "unavailable" (RFC 9309 allows that reading).
        return RobotsFetchResult(status=hop.status)

    # ---- one HTTP exchange

    async def _get(self, url: str, *, accept: str, limit: int, truncate: bool = False) -> _Hop:
        host = _check_url(url)
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": accept,
            "Accept-Language": ACCEPT_LANGUAGE,
        }
        async with self._host_slot(host):
            started = self._monotonic()
            try:
                async with self._client.stream(
                    "GET", url, headers=headers, timeout=TIMEOUT, follow_redirects=False
                ) as resp:
                    hop = _Hop(url=url, status=resp.status_code, headers=resp.headers)
                    if resp.status_code not in REDIRECT_CODES:
                        hop.body, hop.truncated = await _read_capped(
                            resp, url, limit=limit, truncate=truncate
                        )
                        hop.encoding = resp.charset_encoding
            except httpx.TimeoutException as exc:
                raise TransientFetchError(f"timeout ({type(exc).__name__})", url=url) from exc
            except httpx.TransportError as exc:
                raise TransientFetchError(f"network error ({type(exc).__name__})", url=url) from exc
            except httpx.HTTPError as exc:
                raise FetchError(f"HTTP error ({type(exc).__name__}: {exc})", url=url) from exc
            hop.ms = int((self._monotonic() - started) * 1000)
            return hop


async def _read_capped(
    resp: httpx.Response, url: str, *, limit: int, truncate: bool
) -> tuple[bytes, bool]:
    """Read the (decoded) body, refusing or truncating beyond `limit` bytes."""
    declared = resp.headers.get("content-length")
    if not truncate and declared and declared.isdigit() and int(declared) > limit:
        raise FetchError(
            f"body too large ({declared} bytes > {limit})", status=resp.status_code, url=url
        )
    chunks: list[bytes] = []
    size = 0
    async for chunk in resp.aiter_bytes():
        size += len(chunk)
        if size > limit:
            if truncate:
                chunks.append(chunk[: len(chunk) - (size - limit)])
                return b"".join(chunks), True
            raise FetchError(f"body too large (> {limit} bytes)", status=resp.status_code, url=url)
        chunks.append(chunk)
    return b"".join(chunks), False
