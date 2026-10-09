"""Verification of agent claims: our own fetch must find the literal quote. CONTRACT.

For each ClaimDraft of a subject:
1. The draft must fit the vocabulary (research.fields) and the quote rules: 20..400 characters,
   no ellipsis, an https URL.
2. The cited URL is fetched with the project's Fetcher (robots.txt, per-host politeness, bot
   walls are never evaded). Only when that does not verify, up to MAX_GROUNDING_TRIES grounding
   sources of the same site are tried (they are vertexaisearch redirects; the fetcher follows
   them and checks robots.txt on every hop).
3. The quote must appear (text.contains_normalized) in the page's block-aware text
   (text.html_to_lines), or in its raw text nodes (text.text_node_lines) as a fallback for
   script-heavy pages. Non-HTML content (PDF...) is "unsupported_content".
4. Source class of the page that verified: forum first (community.*, reddit...), official when
   the host is one of the subject's official domains (or europa.eu), press otherwise.

Fetched pages are never sent back to the model. Raw pages can be kept (store_raw) for audits.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import urlsplit, urlunsplit

from haggler.fetch import Fetcher
from haggler.fetch.errors import BlockedError, FetchError, RobotsDisallowedError
from haggler.llm.grounding import GroundingSource
from haggler.models.research import (
    MAX_QUOTE,
    CheckStatus,
    Claim,
    ClaimDraft,
    SourceCheck,
    SourceClass,
    SubjectFacts,
    SubjectKind,
    UnverifiedClaim,
    claim_hash,
    claim_id,
)
from haggler.research.fields import FIELD_SPECS, FIELDS_BY_LEVEL, normalize_value, parse_event_date
from haggler.text import contains_normalized, html_to_lines, text_hash, text_node_lines

MIN_QUOTE = 20
MAX_GROUNDING_TRIES = 3
ALWAYS_OFFICIAL = ("europa.eu",)
FORUM_HOSTS = (
    "reddit.com",
    "quora.com",
    "stackexchange.com",
    "discussions.apple.com",
    "answers.microsoft.com",
    "tripadvisor.com",
)
FORUM_PREFIXES = ("community.", "communities.", "forum.", "forums.")
# Paths of community threads hosted on an official domain (e.g. support.google.com/.../thread/).
FORUM_PATHS = re.compile(r"/(thread|threads|community|discussions?)/", re.I)
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_ELLIPSIS = re.compile(r"\.\.\.|…|\[\s*\.\.\.\s*\]")

StoreRaw = Callable[[str, str], str | None]


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def host_matches(host: str, domains: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def site_of(host: str) -> str:
    """Rough registrable domain (last two labels; three for co.uk-style suffixes)."""
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "gov"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def source_class(url: str, official_domains: Iterable[str]) -> SourceClass:
    host = host_of(url)
    path = urlsplit(url).path
    if (
        host.startswith(FORUM_PREFIXES)
        or host_matches(host, FORUM_HOSTS)
        or FORUM_PATHS.search(path)
    ):
        return "forum"
    if host_matches(host, [*official_domains, *ALWAYS_OFFICIAL]):
        return "official"
    return "press"


def canonical_url(url: str) -> str:
    """URL without fragment (the claim hash and the published evidence use it)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


@dataclass(frozen=True)
class ValidDraft:
    field: str
    value: str
    event_date: date | None
    quote: str
    url: str


def validate_draft(draft: ClaimDraft, kind: SubjectKind, *, today: date) -> ValidDraft | str:
    """The normalized draft, or the reason it is invalid."""
    name = draft.field.strip()
    if name not in FIELDS_BY_LEVEL[kind]:
        return f"field {name!r} is not allowed for {kind}"
    spec = FIELD_SPECS[name]
    value = normalize_value(spec, draft.value)
    if value is None:
        return f"value {draft.value!r} is not allowed for {name}"
    event = parse_event_date(draft.event_date) if draft.event_date.strip() else None
    if spec.needs_date and event is None:
        return f"{name} needs an event_date"
    if event is not None and event > today:
        return "event_date in the future"
    quote = " ".join(draft.quote.split())
    if len(quote) < MIN_QUOTE:
        return "quote too short"
    if len(quote) > MAX_QUOTE:
        return "quote too long"
    if _ELLIPSIS.search(quote):
        return "quote has an ellipsis"
    url = draft.url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return "url is not https"
    return ValidDraft(name, value, event if spec.needs_date else None, quote, url)


@dataclass
class Page:
    url: str
    status: CheckStatus | None = None  # None = fetched fine
    final_url: str | None = None
    http_status: int | None = None
    text: str = ""
    content_type: str = ""
    retrieved_at: datetime | None = None
    detail: str = ""
    page_hash: str | None = None
    raw_ref: str | None = None
    _lines: list[str] | None = field(default=None, repr=False)
    _nodes: list[str] | None = field(default=None, repr=False)

    def contains(self, quote: str) -> bool:
        if self.content_type == "text/plain":
            return contains_normalized(self.text, quote)
        if self._lines is None:
            self._lines = html_to_lines(self.text)
        if contains_normalized("\n".join(self._lines), quote):
            return True
        if self._nodes is None:
            self._nodes = text_node_lines(self.text)
        return contains_normalized("\n".join(self._nodes), quote)


class PageCache:
    """One fetch per URL per run, shared by every subject."""

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        timeout_s: float = 60.0,
        store_raw: StoreRaw | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.fetcher = fetcher
        self.timeout_s = timeout_s
        self.store_raw = store_raw
        self.now = now
        self._pages: dict[str, asyncio.Task[Page]] = {}
        self.fetches = 0

    def get(self, url: str) -> Awaitable[Page]:
        if url not in self._pages:
            self._pages[url] = asyncio.ensure_future(self._fetch(url))
        return self._pages[url]

    async def _fetch(self, url: str) -> Page:
        page = Page(url=url)
        self.fetches += 1
        try:
            resp = await asyncio.wait_for(self.fetcher.fetch(url), timeout=self.timeout_s)
        except TimeoutError:
            page.status, page.detail = "fetch_failed", f"timeout after {self.timeout_s:g}s"
            return page
        except RobotsDisallowedError as exc:
            page.status, page.detail = "robots_disallowed", str(exc)[:200]
            return page
        except BlockedError as exc:
            page.status, page.detail, page.http_status = "blocked", str(exc)[:200], exc.status
            return page
        except FetchError as exc:
            page.http_status = exc.status
            page.status = "not_found" if exc.status in (404, 410) else "fetch_failed"
            page.detail = str(exc)[:200]
            return page
        except Exception as exc:  # a fetcher bug must not fail the run
            page.status, page.detail = "fetch_failed", f"{type(exc).__name__}: {exc}"[:200]
            return page
        page.final_url = resp.final_url or url
        page.http_status = resp.status_code
        page.content_type = (resp.content_type or "").lower()
        page.retrieved_at = resp.fetched_at or self.now()
        if not page.content_type.startswith(TEXT_TYPES):
            page.status, page.detail = "unsupported_content", page.content_type
            return page
        page.text = resp.text
        page.page_hash = text_hash(resp.text)
        if self.store_raw is not None:
            try:
                page.raw_ref = self.store_raw(page.final_url, resp.text)
            except Exception as exc:  # keeping the raw page is best effort
                page.detail = f"raw page not stored: {type(exc).__name__}"
        return page


@dataclass
class Verification:
    claims: list[Claim] = field(default_factory=list)
    unverified: list[UnverifiedClaim] = field(default_factory=list)
    # (source class, check status) of every draft, for the run report.
    checks: list[tuple[str, str]] = field(default_factory=list)


def _check(
    page: Page, cited: str, cls: SourceClass, status: CheckStatus, **kw: object
) -> SourceCheck:
    return SourceCheck(
        cited_url=cited,
        final_url=page.final_url,
        source_class=cls,
        status=status,
        http_status=page.http_status,
        page_hash=page.page_hash,
        retrieved_at=page.retrieved_at,
        detail=page.detail,
        **kw,  # type: ignore[arg-type]
    )


async def verify_subject(
    drafts: list[ClaimDraft],
    *,
    key: str,
    kind: SubjectKind,
    official_domains: list[str],
    grounding: list[GroundingSource],
    pages: PageCache,
    today: date,
    previous: SubjectFacts | None = None,
) -> Verification:
    out = Verification()
    first_seen = {c.id: c.first_seen for c in (previous.claims if previous else [])}
    kept: dict[str, Claim] = {}
    for draft in drafts:
        valid = validate_draft(draft, kind, today=today)
        if isinstance(valid, str):
            out.unverified.append(
                UnverifiedClaim(
                    field=draft.field[:60],
                    value=draft.value[:120],
                    event_date=draft.event_date[:10],
                    quote=" ".join(draft.quote.split())[:MAX_QUOTE],
                    cited_url=draft.url[:500],
                    status="invalid",
                    detail=valid,
                )
            )
            out.checks.append((source_class(draft.url, official_domains), "invalid"))
            continue
        page = await pages.get(valid.url)
        status: CheckStatus = page.status or (
            "verified" if page.contains(valid.quote) else "quote_not_found"
        )
        via_grounding = False
        if status != "verified":
            site = site_of(host_of(valid.url))
            tries = [
                g.uri
                for g in grounding
                if site and site_of((g.domain or g.title or host_of(g.uri)).lower()) == site
            ][:MAX_GROUNDING_TRIES]
            for uri in tries:
                alt = await pages.get(uri)
                if (
                    alt.status is None
                    and site_of(host_of(alt.final_url or "")) == site
                    and (alt.contains(valid.quote))
                ):
                    page, status, via_grounding = alt, "verified", True
                    break
        final = page.final_url or valid.url
        cls = source_class(final if status == "verified" else valid.url, official_domains)
        out.checks.append((cls, status))
        if status != "verified":
            out.unverified.append(
                UnverifiedClaim(
                    field=valid.field,
                    value=valid.value,
                    event_date=valid.event_date.isoformat() if valid.event_date else "",
                    quote=valid.quote,
                    cited_url=valid.url,
                    status=status,
                    detail=page.detail,
                )
            )
            continue
        cid = claim_id(key, valid.field, valid.value, valid.event_date)
        url = canonical_url(final)
        claim = Claim(
            id=cid,
            hash=claim_hash(valid.field, valid.value, valid.event_date, valid.quote, url),
            field=valid.field,
            value=valid.value,
            event_date=valid.event_date,
            quote=valid.quote,
            check=_check(page, valid.url, cls, "verified", via_grounding=via_grounding).model_copy(
                update={"final_url": url}
            ),
            first_seen=first_seen.get(cid, today),
        )
        # One claim per id: an official source beats a press one, the first one wins ties.
        prev = kept.get(cid)
        if prev is None or (not prev.official and claim.official):
            kept[cid] = claim
    out.claims = sorted(kept.values(), key=lambda c: c.id)
    return out


async def recheck_claims(
    claims: list[Claim], *, pages: PageCache, official_domains: list[str]
) -> tuple[list[Claim], list[UnverifiedClaim]]:
    """Previous facts the agent did not report again: re-fetch their page. A quote that is gone
    from a page that loaded drops the fact; a page that failed to load keeps it as it was."""
    kept: list[Claim] = []
    dropped: list[UnverifiedClaim] = []
    for c in claims:
        page = await pages.get(c.url)
        if page.status is not None:
            kept.append(c)
            continue
        if page.contains(c.quote) and canonical_url(page.final_url or c.url) == c.url:
            kept.append(
                c.model_copy(
                    update={
                        "check": c.check.model_copy(
                            update={
                                "retrieved_at": page.retrieved_at,
                                "page_hash": page.page_hash,
                                "http_status": page.http_status,
                                "source_class": source_class(c.url, official_domains),
                            }
                        )
                    }
                )
            )
            continue
        dropped.append(
            UnverifiedClaim(
                field=c.field,
                value=c.value,
                event_date=c.event_date.isoformat() if c.event_date else "",
                quote=c.quote,
                cited_url=c.url,
                status="quote_not_found",
                detail="no longer on the page",
            )
        )
    return kept, dropped
