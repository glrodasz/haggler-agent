"""Verification of agent claims: our own fetch must find the literal quote. CONTRACT.

For each ClaimDraft of a subject:
1. The draft must fit the vocabulary (research.fields) and the quote rules: 20..400 characters,
   no ellipsis, an https URL.
2. The cited URL is fetched with the project's Fetcher (robots.txt, per-host politeness, bot
   walls are never evaded). Only when that does not verify: up to MAX_GROUNDING_TRIES grounding
   sources of the same site that are direct links, then the subject's known official pages
   (catalog research_urls). Grounding sources are usually vertexaisearch.cloud.google.com
   redirects, whose robots.txt disallows our user agent (probe, 2026-10-09): they are never
   fetched, and a claim citing one is checked against the fallbacks only.
3. The quote must appear (text.contains_normalized) in the page's block-aware text
   (text.html_to_lines), or in its raw text nodes (text.text_node_lines) as a fallback for
   script-heavy pages. A PDF (haggler.fetch.pdf, in a sandboxed worker) is read as its
   extracted text: layout-mode lines first, then plain-mode lines as the fallback; its page_hash
   is over that text. A PDF that cannot be read (encrypted, no text layer, over a cap, broken)
   is "unsupported_content" with the pdf_* reason in the detail, as is any other non-text
   content. (Until Phase 3 every PDF was "unsupported_content": a quote in an official PDF now
   verifies.) Embedded JSON (Page.contains(allow_embedded=True)) is never used here.
4. Source class of the page that verified: forum first (community.*, reddit..., and user or
   third-party content on official domains: groups/sites/docs.google.com, youtube.com/watch,
   apps.apple.com...), official when the host is one of the subject's official domains, press
   otherwise. Model-chosen URLs must name a public host (no IP literal or internal name). An
   official domain entry may carry a path prefix ("www.apple.com/se/", a bundle provider's
   domains for one country): the page's path must then be under it.
5. Bundle subjects (research.kinds "bundle"): a field that is official_only (the bundle VALUES
   of research.fields) only verifies on an official page (the provider's domains for the
   bundle's country): a quote found elsewhere is looked for on the fallbacks, and stays
   "invalid" when no official page has it. Then, ONLY for a bundle value field with a
   value_check, the value hook: research.values.check_value with the field's kind, the page's
   context lines around the quote (Page.locate) and the bundle (ValueContext: country, currency,
   period, provider, its sibling bundles, catalog and the FX table when there is one) must find
   the claimed value in the quote; else the claim is "value_not_in_quote" or "implausible" and,
   like any other failed check, not persisted. A url value must also be the document the quote
   came from. A value more than research.values.BIG_CHANGE away from the YAML value keeps
   Claim.big_change (and is logged). When the quote states its value only with the lines above
   it (a tier named in a heading) and validate-catalog would refuse it as an anchor's text, the
   claim keeps Claim.anchor_text, the excerpt from that heading to the quote (anchor_span), for
   the review snippet. The bundle's feasibility twins take the Phase 2 checks only.

Fetched pages (PDF text included) are never sent back to the model. Raw pages can be kept
(store_raw: the text of an HTML page, the bytes of a PDF) for audits.
"""

from __future__ import annotations

import asyncio
import bisect
import ipaddress
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from haggler.fetch import Fetcher, RawResponse
from haggler.fetch.errors import BlockedError, FetchError, RobotsDisallowedError
from haggler.fetch.pdf import extract_pdf_text, is_pdf_response
from haggler.fetch.urls import resolve_dot_segments
from haggler.fetch.walls import THIN_TEXT_CHARS, challenge_headers, login_wall, page_wall
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
from haggler.money import parse_money_code
from haggler.research.fields import FIELD_SPECS, FIELDS_BY_LEVEL, normalize_value, parse_event_date
from haggler.research.values import ValueCheck, ValueKind, check_value
from haggler.text import (
    contains_normalized,
    html_to_lines,
    json_scripts,
    normalize_for_match,
    page_title,
    text_hash,
    text_node_lines,
)

if TYPE_CHECKING:
    from haggler.models.catalog import Catalog
    from haggler.models.curated import Bundle, Provider
    from haggler.models.fx import FxTable

log = logging.getLogger(__name__)

MIN_QUOTE = 20
MAX_GROUNDING_TRIES = 3
# Search redirect hosts: their robots.txt disallows crawlers, and they are not the page itself.
REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)
# Pages written by users or third parties, even on a service's own domain: never "official".
FORUM_HOSTS = (
    "reddit.com",
    "quora.com",
    "stackexchange.com",
    "tripadvisor.com",
    "discussions.apple.com",
    "apps.apple.com",
    "podcasts.apple.com",
    "answers.microsoft.com",
    "techcommunity.microsoft.com",
    "groups.google.com",
    "sites.google.com",
    "docs.google.com",
    "drive.google.com",
    "play.google.com",
)
FORUM_PREFIXES = ("community.", "communities.", "forum.", "forums.")
# Community threads and user content hosted on an official domain
# (support.google.com/.../thread/..., learn.microsoft.com/.../answers/..., youtube.com/watch...).
FORUM_PATHS = re.compile(
    r"/(thread|threads|community|discussions?|answers|questions)(/|$)"
    r"|^/(watch|shorts|channel|c|user|post|playlist)(/|$)|^/@",
    re.I,
)
_SOURCE_ORDER = {"forum": 0, "press": 1, "official": 2}
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_ELLIPSIS = re.compile(r"\.\.\.|…|\[\s*\.\.\.\s*\]")

# Keeps a fetched page for audits: (final URL, HTML text or PDF bytes) -> store key.
StoreRaw = Callable[[str, str | bytes], str | None]
# Where Page.contains found a quote: the rendered lines (block-aware HTML, a PDF's layout-mode
# lines, text/plain lines), the fallback lines (HTML text nodes, a PDF's plain-mode lines) or,
# only when asked, the string leaves of embedded JSON scripts.
Via = Literal["lines", "nodes", "embedded"]
# Context Page.locate returns around a quote: lines before its first line and after its last.
CONTEXT_BEFORE = 2
CONTEXT_AFTER = 1
MAX_JSON_NODES = 200_000


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def host_matches(host: str, domains: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def on_official(url: str, domains: Iterable[str]) -> bool:
    """The URL's host is one of `domains` (or a subdomain); an entry with a path prefix
    ("www.apple.com/se/") also needs the URL's path under it (haggler.catalog.on_domains), with
    its dot segments resolved ("/se/../co/" is a /co/ page)."""
    url = resolve_dot_segments(url)
    host = host_of(url)
    path = urlsplit(url).path
    for entry in domains:
        domain, slash, rest = entry.partition("/")
        if not host_matches(host, (domain,)):
            continue
        prefix = slash + rest
        if not slash or path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False


def site_of(host: str) -> str:
    """Rough registrable domain (last two labels; three for co.uk-style suffixes)."""
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "gov"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def source_class(url: str, official_domains: Iterable[str]) -> SourceClass:
    url = resolve_dot_segments(url)  # the page really fetched (fetch.urls)
    host = host_of(url)
    path = urlsplit(url).path
    if (
        host.startswith(FORUM_PREFIXES)
        or host_matches(host, FORUM_HOSTS)
        or FORUM_PATHS.search(path)
    ):
        return "forum"
    if on_official(url, official_domains):
        return "official"
    return "press"


def public_host(host: str) -> bool:
    """A public DNS name: never an IP literal, localhost or an internal name (the URLs come from
    the model, and the job runs inside a cloud project)."""
    host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False
    except ValueError:
        pass
    if "." not in host or host == "localhost":
        return False
    return not host.endswith((".internal", ".local", ".localhost", ".lan", ".home.arpa"))


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
    if not public_host(parts.hostname):
        return "url host is not a public domain"
    return ValidDraft(name, value, event if spec.needs_date else None, quote, url)


def json_text_leaves(html: str) -> list[str]:
    """The string leaves (whitespace collapsed) of a page's ld+json, __NEXT_DATA__ and other
    application/json scripts, depth first, at most MAX_JSON_NODES nodes per script."""
    out: list[str] = []
    for _ident, value in json_scripts(html):
        stack: list[Any] = [value]
        visited = 0
        while stack and visited < MAX_JSON_NODES:
            node = stack.pop()
            visited += 1
            if isinstance(node, dict):
                stack.extend(reversed(list(node.values())))
            elif isinstance(node, list):
                stack.extend(reversed(node))
            elif isinstance(node, str) and (text := " ".join(node.split())):
                out.append(text)
    return out


def _locate(lines: list[str], needle: str) -> tuple[int, list[str]] | None:
    """(index of the line where the normalized `needle` starts, that line and the ones it
    spans with CONTEXT_BEFORE lines before and CONTEXT_AFTER after), or None. Lines are joined
    the way contains_normalized joins them (any whitespace run is one space)."""
    starts: list[int] = []
    owners: list[int] = []
    parts: list[str] = []
    pos = 0
    for i, line in enumerate(lines):
        norm = normalize_for_match(line)
        if not norm:
            continue
        starts.append(pos)
        owners.append(i)
        parts.append(norm)
        pos += len(norm) + 1
    at = " ".join(parts).find(needle)
    if at < 0:
        return None
    first = owners[bisect.bisect_right(starts, at) - 1]
    last = owners[bisect.bisect_right(starts, at + len(needle) - 1) - 1]
    return first, lines[max(0, first - CONTEXT_BEFORE) : last + 1 + CONTEXT_AFTER]


@dataclass
class Page:
    url: str
    status: CheckStatus | None = None  # None = fetched fine
    final_url: str | None = None
    http_status: int | None = None
    # HTML or plain text as fetched; for a PDF, its extracted (layout-mode) lines.
    text: str = ""
    content_type: str = ""
    retrieved_at: datetime | None = None
    detail: str = ""
    page_hash: str | None = None
    raw_ref: str | None = None
    is_pdf: bool = False
    pdf_pages: int = 0
    # The response headers a bot-wall test reads (haggler.fetch.walls.CHALLENGE_HEADERS).
    headers: dict[str, str] = field(default_factory=dict)
    _lines: list[str] | None = field(default=None, repr=False)
    _nodes: list[str] | None = field(default=None, repr=False)
    _embedded: list[str] | None = field(default=None, repr=False)

    def lines(self) -> list[str]:
        """The page's rendered lines: block-aware HTML lines, a PDF's layout-mode lines, or the
        non-empty lines of a text/plain page."""
        if self._lines is None:
            if self.content_type == "text/plain":
                self._lines = [
                    ln for ln in (" ".join(r.split()) for r in self.text.split("\n")) if ln
                ]
            else:
                self._lines = html_to_lines(self.text)
        return self._lines

    def nodes(self) -> list[str]:
        """The fallback lines: raw HTML text nodes, a PDF's plain-mode lines (none for
        text/plain)."""
        if self._nodes is None:
            self._nodes = [] if self.content_type == "text/plain" else text_node_lines(self.text)
        return self._nodes

    def embedded(self) -> list[str]:
        """String leaves of the HTML page's embedded JSON scripts (none for PDF or text/plain)."""
        if self._embedded is None:
            plain = self.is_pdf or self.content_type == "text/plain"
            self._embedded = [] if plain else json_text_leaves(self.text)
        return self._embedded

    def contains(self, quote: str, *, allow_embedded: bool = False) -> Via | None:
        """Where the quote is (a truthy Via), or None. Embedded JSON is searched only with
        allow_embedded=True, and a match there alone never fixes a price or a tier."""
        if contains_normalized("\n".join(self.lines()), quote):
            return "lines"
        if contains_normalized("\n".join(self.nodes()), quote):
            return "nodes"
        if allow_embedded and contains_normalized("\n".join(self.embedded()), quote):
            return "embedded"
        return None

    def wall(self) -> str | None:
        """What wall a page that loaded is, or None (haggler.fetch.walls): "login" when its final
        URL is a sign-in or account-verification page the requested URL was not; else the
        bot-wall vendor of a definite challenge header, or of a marker in the title or visible
        text of a thin HTML page (a challenge served with 200)."""
        if self.status is not None:
            return None
        if login_wall(self.url, self.final_url or self.url):
            return "login"
        if self.is_pdf or self.content_type == "text/plain":
            return page_wall(self.headers, "", [], False)
        lines = self.lines()
        thin = sum(len(ln) for ln in lines) < THIN_TEXT_CHARS
        return page_wall(self.headers, page_title(self.text) if thin else "", lines, thin)

    def locate(self, quote: str) -> tuple[int, list[str]] | None:
        """(index of the line where the quote starts, context lines: CONTEXT_BEFORE before
        the quote's lines and CONTEXT_AFTER after), searched in lines() then nodes() (the index
        refers to the list that matched), or None where contains() finds neither."""
        needle = normalize_for_match(quote)
        if not needle:
            return None
        for lines in (self.lines(), self.nodes()):
            if (hit := _locate(lines, needle)) is not None:
                return hit
        return None


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
        page.headers = challenge_headers(resp.headers or {})
        # Before the text types: a PDF served without a content type gets the Accept fallback.
        if is_pdf_response(resp):
            return await self._read_pdf(page, resp)
        if not page.content_type.startswith(TEXT_TYPES):
            page.status, page.detail = "unsupported_content", page.content_type
            return page
        page.text = resp.text
        page.page_hash = text_hash(resp.text)
        self._keep_raw(page, resp.text)
        return page

    async def _read_pdf(self, page: Page, resp: RawResponse) -> Page:
        page.is_pdf = True
        try:
            pdf = await extract_pdf_text(resp.body)
        except Exception as exc:  # extract_pdf_text never raises for a bad PDF: this is a bug
            page.status, page.detail = "unsupported_content", f"pdf_error: {type(exc).__name__}"
            return page
        page.pdf_pages = pdf.pages
        if not pdf.ok:
            page.status, page.detail = "unsupported_content", pdf.reason
            return page
        page.text = pdf.text
        page._lines, page._nodes = list(pdf.lines), list(pdf.stream_lines)
        page.page_hash = text_hash(page.text)
        self._keep_raw(page, resp.body)
        return page

    def _keep_raw(self, page: Page, data: str | bytes) -> None:
        if self.store_raw is None:
            return
        try:
            page.raw_ref = self.store_raw(page.final_url or page.url, data)
        except Exception as exc:  # keeping the raw page is best effort
            page.detail = f"raw page not stored: {type(exc).__name__}"


# research.fields value field -> the bundles.yaml price attribute its big_change compares with.
_CURRENT_PRICE = {
    "bundle_price": "amount",
    "bundle_intro_price": "intro_amount",
    "bundle_base_price": "base_plan_amount",
}


# The check validate-catalog runs on an anchor's text alone (haggler.catalog), per bundle value
# field an anchor fact confirms (proposals.ANCHOR_FACT).
OFFLINE_KIND: dict[str, ValueKind] = {
    "bundle_price": "money",
    "bundle_intro_price": "intro_money",
    "bundle_base_price": "base_money",
    "bundle_includes": "component",
    "bundle_discount": "component",
    "bundle_intro_until": "date_until",
    "bundle_offer_until": "date_until",
}
_OFFLINE_MONEY = frozenset({"money", "intro_money", "base_money"})


@dataclass(frozen=True)
class ValueContext:
    """What the bundle value hook checks one bundle subject's value claims against."""

    bundle: Bundle
    provider: Provider | None
    # Every curated bundle (the tier rule looks for the bundle's siblings among them).
    bundles: tuple[Bundle, ...]
    catalog: Catalog | None = None
    fx: FxTable | None = None

    def current(self, field_name: str) -> object:
        """The YAML value a researched one would replace (money fields: big_change)."""
        attr = _CURRENT_PRICE.get(field_name)
        return getattr(self.bundle.price, attr) if attr else None

    def check(self, field_name: str, value: str, quote: str, page: Page) -> ValueCheck:
        """check_value of the field's kind on `quote` found on `page`, with the page's context
        lines; a url value must be that page (as requested or after its redirects)."""
        kind = FIELD_SPECS[field_name].value_check
        if kind is None:
            return ValueCheck("ok", "no value check for this field")
        b = self.bundle
        located = page.locate(quote)
        url = page.final_url or page.url
        result = check_value(
            kind,
            value,
            quote,
            context_lines=located[1] if located else (),
            country=b.country,
            currency=b.price.currency,
            period=b.price.period,
            provider=self.provider,
            bundle=b,
            bundles=self.bundles,
            catalog=self.catalog,
            fx=self.fx,
            current=self.current(field_name),
            url=url,
        )
        if result.ok and kind == "url":
            documents = {canonical_url(url), canonical_url(page.url)}
            if canonical_url(value) not in documents:
                return ValueCheck(
                    "value_not_in_quote", f"the quote is not in the document it names ({value})"
                )
        return result

    def offline(self, field_name: str, value: str, text: str) -> ValueCheck | None:
        """`value` checked on `text` ALONE, the way validate-catalog checks an anchor's text
        (haggler.catalog: its line breaks kept, no page context, no FX table); None for a
        field no anchor fact confirms."""
        from haggler.catalog import anchor_lines

        kind = OFFLINE_KIND.get(field_name)
        if kind is None:
            return None
        b = self.bundle
        price = b.price
        checked: object = value
        period = price.period
        if kind in _OFFLINE_MONEY:
            parsed = parse_money_code(value)
            if parsed is None:
                return ValueCheck("value_not_in_quote", f"not an amount: {value}")
            checked, period = parsed[0], parsed[2] or price.period
        return check_value(
            kind,
            checked,
            anchor_lines(text),
            country=b.country,
            currency=price.currency,
            period=period,
            provider=self.provider,
            bundle=b,
            bundles=self.bundles or None,
            catalog=self.catalog,
        )


def anchor_span(page: Page, quote: str, accepts: Callable[[str], bool]) -> str | None:
    """An anchor text for a quote that states its value only with the page lines above it (the
    tier named in a heading, as on Meli+ CO's plans page): the shortest text of whole lines
    above the quote plus the quote, one per line, that is on the page as one piece, has at most
    MAX_QUOTE characters and passes `accepts` (validate-catalog's offline check of an anchor's
    text, ValueContext.offline). None when the quote passes alone, or when no such text does."""
    flat = " ".join(quote.split())
    if not flat or accepts(flat):
        return None
    located = page.locate(flat)
    if located is None:
        return None
    first, context = located
    above = [" ".join(ln.split()) for ln in context[: min(first, CONTEXT_BEFORE)]]
    for start in range(len(above) - 1, -1, -1):
        text = "\n".join([*(ln for ln in above[start:] if ln), flat])
        if len(" ".join(text.split())) > MAX_QUOTE:
            break
        if page.contains(text) and accepts(text):
            return text
    return None


@dataclass
class Verification:
    claims: list[Claim] = field(default_factory=list)
    unverified: list[UnverifiedClaim] = field(default_factory=list)
    # (source class, check status) of every draft, for the run report.
    checks: list[tuple[str, str]] = field(default_factory=list)


def _accepted(check: ValueCheck | None) -> bool:
    return check is not None and check.ok


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
    known_pages: list[str] | None = None,
    value_context: ValueContext | None = None,
) -> Verification:
    """`value_context`: the bundle of a bundle subject (the value hook, rule 5); a bundle value
    claim without one fails closed (value_not_in_quote)."""
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
        spec = FIELD_SPECS[valid.field]

        def verifies(p: Page, spec=spec, valid=valid) -> bool:
            """The quote is on the page, and the page is official when the field needs it."""
            if not p.contains(valid.quote):
                return False
            return not spec.official_only or (
                source_class(p.final_url or p.url, official_domains) == "official"
            )

        if host_matches(host_of(valid.url), REDIRECT_HOSTS):
            page = Page(
                url=valid.url, status="invalid", detail="search redirect link, not the page"
            )
        else:
            page = await pages.get(valid.url)
        status: CheckStatus = page.status or ("verified" if verifies(page) else "quote_not_found")
        detail = page.detail
        if status == "quote_not_found" and page.contains(valid.quote):
            status = "invalid"
            detail = f"{valid.field}: only an official page of the subject states it"
        via = "cited"
        if status != "verified":
            site = site_of(host_of(valid.url))
            fallbacks = [
                ("grounding", g.uri)
                for g in grounding
                if site
                and not host_matches(host_of(g.uri), REDIRECT_HOSTS)
                and site_of((g.domain or g.title or host_of(g.uri)).lower()) == site
            ][:MAX_GROUNDING_TRIES]
            fallbacks += [("known_page", u) for u in known_pages or [] if u != valid.url]
            for kind_, uri in fallbacks:
                alt = await pages.get(uri)
                if alt.status is not None or not verifies(alt):
                    continue
                if kind_ == "grounding" and site_of(host_of(alt.final_url or "")) != site:
                    continue
                page, status, via, detail = alt, "verified", kind_, alt.detail
                break
        big_change = False
        anchor_text: str | None = None
        if status == "verified" and kind == "bundle" and spec.value_check is not None:
            # The value hook (rule 5): the quote must state the claimed value.
            if value_context is None:
                status, detail = "value_not_in_quote", "no curated bundle to check the value"
            else:
                vc = value_context.check(valid.field, valid.value, valid.quote, page)
                if not vc.ok:
                    status, detail = vc.status, vc.reason
                else:
                    if vc.big_change:
                        big_change = True
                        log.info(
                            "%s %s=%s: big change (%s)", key, valid.field, valid.value, vc.reason
                        )
                    anchor_text = anchor_span(
                        page,
                        valid.quote,
                        lambda t, ctx=value_context, v=valid: _accepted(
                            ctx.offline(v.field, v.value, t)
                        ),
                    )
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
                    detail=detail[:300],
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
            check=_check(page, valid.url, cls, "verified", via=via).model_copy(
                update={"final_url": url}
            ),
            first_seen=first_seen.get(cid, today),
            big_change=big_change,
            anchor_text=anchor_text,
        )
        # One claim per id: official beats press beats forum; the first one wins ties.
        prev = kept.get(cid)
        rank = _SOURCE_ORDER[claim.check.source_class]
        if prev is None or rank > _SOURCE_ORDER[prev.check.source_class]:
            kept[cid] = claim
    out.claims = sorted(kept.values(), key=lambda c: c.id)
    return out


async def reverify(
    c: Claim,
    *,
    pages: PageCache,
    official_domains: list[str],
    value_context: ValueContext | None = None,
) -> tuple[str, Claim | None]:
    """Re-fetch a stored claim's page: ("verified", refreshed claim), ("unchecked", claim) when
    the page could not be read today (also a wall that answered 200 instead of the page),
    ("gone", None) for 404/410, ("changed", None) when the page loaded without the quote (or now
    redirects elsewhere). With `value_context` (a bundle
    subject), a wall that answered 200 (Page.wall: a sign-in or account-verification page, a bot
    challenge) is a page that could not be read ("unchecked"), and a value claim's quote must
    also still state its value on the page as it is today: else (the check's status, None)."""
    page = await pages.get(c.url)
    if page.status == "not_found":
        return "gone", None
    if page.status is not None or (value_context is not None and page.wall() is not None):
        return "unchecked", c
    if not (page.contains(c.quote) and canonical_url(page.final_url or c.url) == c.url):
        # A wall that answered 200 is a page we could not read, not one without the quote.
        if page.wall() is not None:
            return "unchecked", c
        return "changed", None
    spec = FIELD_SPECS.get(c.field)
    big_change = c.big_change
    if value_context is not None and spec is not None and spec.value_check is not None:
        vc = value_context.check(c.field, c.value, c.quote, page)
        if not vc.ok:
            return vc.status, None
        big_change = vc.big_change
    check = c.check.model_copy(
        update={
            "retrieved_at": page.retrieved_at,
            "page_hash": page.page_hash,
            "http_status": page.http_status,
            "source_class": source_class(c.url, official_domains),
        }
    )
    return "verified", c.model_copy(update={"check": check, "big_change": big_change})


_DROPPED: dict[str, tuple[CheckStatus, str]] = {
    "gone": ("not_found", "the page no longer exists"),
    "changed": ("quote_not_found", "no longer on the page"),
    "value_not_in_quote": ("value_not_in_quote", "the quote no longer states the value"),
    "implausible": ("implausible", "the value is no longer plausible"),
}


async def recheck_claims(
    claims: list[Claim],
    *,
    pages: PageCache,
    official_domains: list[str],
    value_context: ValueContext | None = None,
) -> tuple[list[Claim], list[UnverifiedClaim]]:
    """Previous facts the agent did not report again: re-fetch their page. A quote that is gone
    from a page that loaded, or a page that no longer exists, drops the fact; a page that could
    not be read keeps it as it was (the evaluator ignores it once it is a year old). With
    `value_context`, a bundle value that its quote no longer states is dropped too (reverify)."""
    kept: list[Claim] = []
    dropped: list[UnverifiedClaim] = []
    for c in claims:
        status, again = await reverify(
            c, pages=pages, official_domains=official_domains, value_context=value_context
        )
        if again is not None:
            kept.append(again)
            continue
        check_status, detail = _DROPPED.get(status, _DROPPED["changed"])
        dropped.append(
            UnverifiedClaim(
                field=c.field,
                value=c.value,
                event_date=c.event_date.isoformat() if c.event_date else "",
                quote=c.quote,
                cited_url=c.url,
                status=check_status,
                detail=detail,
            )
        )
    return kept, dropped
