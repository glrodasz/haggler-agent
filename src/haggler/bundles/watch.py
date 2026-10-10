"""The bundle watcher: reads the official pages of the watched bundles once a week, without an LLM,
and records what it finds of their anchors (haggler.models.bundles). CONTRACT.

It runs inside the weekly research job (research.nodes, node watch_bundles), always: with the
monthly search guard spent, with --no-llm, with --max-jobs 0 and with --watch-only. It never
writes bundles.yaml: what it finds is evidence (anchor states) or proposals (price hints).

Which bundles: watch.mode auto, with a provider whose terms allow automated access, not expired
(haggler.bundles.status.is_expired). A manual or off bundle (or a forbidden provider: off) is
not fetched and keeps its previous pages, anchors and hints (an anchor's state only while its
anchor_hash is unchanged). An expired bundle keeps its previous state, if it had one.

Fetching: every page through the run's PageCache, the one verify_evidence uses afterwards (one
fetch per URL per run; HttpFetcher: robots.txt on every hop, the identifiable user agent, at
least 3 s between requests to a host, bot walls never evaded, default egress, no proxies). At most
`max_pages` distinct URLs a run, given out in bundles.yaml order: first every watch page (and
the index page of a versioned PDF), then the documents the versioned PDFs point to. A page over
the budget is status over_budget and logged by URL. A paused page (3 unreadable runs in a row,
then 28 days) is not fetched and keeps its previous check and anchors.

A page is read (status ok) when it loaded, it is no wall (research.evidence.Page.wall: a final
URL on a sign-in or account-verification page the watch URL was not, such as Mercado Libre's
/gz/account-verification, or a bot challenge served with 200; else blocked), its final URL is
on the provider's domains for the bundle's country (haggler.catalog.on_domains) and, when it
shows prices, some of them are in the country's currency; otherwise wrong_country
(one.google.com prices by IP). A price whose marker
cannot be that currency ("$" or "kr" on a page of a country whose currency is neither) is
foreign, and so is a "$" amount the country's currency cannot plausibly be ("$9.99" on a COP
page: decimals of a currency without them, or under PLAUSIBLE_MONTHLY_EUR's floor with the FX
table). blocked, robots_disallowed, wrong_country and every other unreadable page make their
anchors unreadable: never "the value changed".

Versioned PDFs (a watch page with index_url and link_pattern): the index is fetched first; its
links (<a href> resolved against the index's final URL, and absolute URLs in its scripts) on the
provider's domains for the country that match link_pattern give editions (the named group
"edition"; compared by their numbers). When the newest one is newer than the curated url's own
edition, the curated document is superseded (not fetched): outcome new_document, the anchors
quoting it are outdated_document, and the newest document is fetched (within the budget) to
report which of those anchors it contains (PageCheck.newest_anchors). When the newest one is the
curated one, the document is current (edition_check "index"; when the index links that edition
at another URL, a re-upload, the document is read there). Without a usable index (unreadable, or
no matching link), a newer edition an earlier run found still supersedes the curated one
(editions only go up); else the document counts as current only when a validity anchor is
verified in it (edition_check "validity": confidence at most medium), else "unchecked".

Anchors (Page.contains(text, allow_embedded=True)): via lines/nodes -> verified, after the
value-quote check against the YAML value (haggler.research.values.check_value with the page
context of Page.locate: price, base_price and intro amounts; a component's service, plan and
discount; a validity anchor's date is checked offline only, its text cannot change); a failed
check -> missing with the reason. Via embedded -> embedded_only (never confirms a value alone).
Absent from a page that was read -> missing (misses + 1). Page not read -> unreadable. A
missing, embedded-only or outdated anchor stays unconfirmed (AnchorCheck.unconfirmed) through
the runs that do not read it, until a read verifies it again.

Price hints: on every page read (and the newest document of a versioned PDF), each amount in
the country's currency that check_value reads as a regular price (or an intro price) of the
bundle and that differs from the YAML value (the regular amount, the base plan and the marginal
amount; the intro amount). In a PDF (a price list of many products, whose columns are not
understood) the line itself must also name the bundle (its aliases or name). An observed price
(price.source observed) comes from the daily data: no regular hints. At most MAX_HINTS per
bundle, deduplicated; a hint found again keeps its first_seen. When the line alone names no tier
(the tier is in a heading above it) and validate-catalog would refuse it as an anchor, the hint
also keeps anchor_text: the excerpt from that heading to the line (research.evidence.
anchor_span), which the review snippet offers. Hints are proposals for review
(haggler.bundles.proposals), never values.

Outcome: changed when a price, base_price or component anchor is missing on a page that was
read; else new_document when a versioned PDF is superseded; else unreadable when no page could
be read (an index page only counts when an anchor quotes it); else ok.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from haggler.bundles.status import is_expired
from haggler.catalog import anchor_lines, on_domains
from haggler.models.bundles import (
    HINT_LINE_CHARS,
    AnchorCheck,
    BundleWatchState,
    EditionCheck,
    PageCheck,
    PageKind,
    PageStatus,
    PriceHint,
    UnconfirmedStatus,
    WatchFile,
    WatchOutcome,
    anchor_hash,
)
from haggler.models.catalog import Catalog
from haggler.models.curated import Anchor, Bundle, BundlesFile, Provider, WatchPage
from haggler.models.fx import FxTable
from haggler.money import ZERO_DECIMAL, Money, find_prices
from haggler.research.evidence import (
    CONTEXT_AFTER,
    CONTEXT_BEFORE,
    Page,
    PageCache,
    anchor_span,
    canonical_url,
)
from haggler.research.values import PLAUSIBLE_MONTHLY_EUR, ValueCheck, ValueKind, check_value
from haggler.text import normalize_for_match

log = logging.getLogger(__name__)

MAX_PAGES = 40
PAUSE_AFTER_UNREADABLE = 3
PAUSE_DAYS = 28
MAX_HINTS = 5
DETAIL_CHARS = 200
# The anchors whose absence from a page that was read means the bundle changed.
DECISIVE_FACTS = frozenset({"price", "base_price", "component"})

_PRICE_FACTS: dict[str, tuple[ValueKind, str]] = {
    "price": ("money", "amount"),
    "base_price": ("base_money", "base_plan_amount"),
    "intro": ("intro_money", "intro_amount"),
}
_COUNTED_INCLUSIONS = ("discount_pct", "discount_abs", "free_months")
_FETCH_STATUS: dict[str, PageStatus] = {
    "blocked": "blocked",
    "robots_disallowed": "robots_disallowed",
    "not_found": "not_found",
    "fetch_failed": "failed",
    "unsupported_content": "unsupported_content",
}
_ABS_URL = re.compile(r"https://[^\s\"'<>\\]+")
_NOT_MARKER = re.compile(r"[\d.,'’\s]")


# ------------------------------------------------------------------ helpers


def _clip(text: str, limit: int = DETAIL_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def edition_key(edition: str) -> tuple[Any, ...]:
    """Editions compare by their numbers ("18" < "19", "18_0701" < "19_0102")."""
    numbers = tuple(int(n) for n in re.findall(r"\d+", edition))
    return (0, numbers, edition) if numbers else (1, (), edition)


def _edition_of(pattern: re.Pattern[str], url: str) -> str | None:
    m = pattern.search(url)
    return m.group("edition") if m else None


def page_links(page: Page) -> list[str]:
    """Absolute URLs a page links to: <a href> resolved against its final URL, then absolute
    https URLs written in its text (scripts, JSON with escaped slashes)."""
    base = page.final_url or page.url
    out: list[str] = []
    if not page.is_pdf:
        try:
            for node in LexborHTMLParser(page.text).css("a[href]"):
                href = (node.attributes.get("href") or "").strip()
                if href and not href.startswith(("javascript:", "mailto:", "#")):
                    out.append(urljoin(base, href))
        except Exception:  # a broken page has no links, it does not fail the watcher
            pass
    out.extend(m.group(0) for m in _ABS_URL.finditer(page.text.replace("\\/", "/")))
    return list(dict.fromkeys(canonical_url(u) for u in out))


def edition_links(page: Page, pattern: re.Pattern[str], domains: Sequence[str]) -> dict[str, str]:
    """{edition: url} of the links of an index page that match `pattern`, on `domains` only."""
    found: dict[str, str] = {}
    for url in page_links(page):
        if not on_domains(url, domains):
            continue
        edition = _edition_of(pattern, url)
        if edition:
            found.setdefault(edition, url)
    return found


def _marker(money: Money) -> str:
    return _NOT_MARKER.sub("", money.raw) or "?"


def _in_currency(money: Money, currency: str, fx: FxTable | None) -> bool:
    """Whether a price can be in `currency`. find_prices resolves "$" to the country's currency
    whenever it is a dollar-sign one (COP, MXN, ...), so a "$" amount it cannot plausibly be is
    another country's price: decimals of a currency without them, or (with an FX table) under
    the floor of PLAUSIBLE_MONTHLY_EUR."""
    if money.currency != currency:
        return False
    if _marker(money) != "$":
        return True
    if currency in ZERO_DECIMAL and money.amount != money.amount.to_integral_value():
        return False
    eur = fx.to_eur(money.amount, currency) if fx is not None else None
    return eur is None or eur >= PLAUSIBLE_MONTHLY_EUR[0]


def foreign_currencies(page: Page, currency: str, fx: FxTable | None = None) -> list[str]:
    """The currencies (or the bare markers) of the page's prices (rendered lines and text
    nodes) when none of them can be in `currency` ([] otherwise). A marker find_prices cannot
    resolve with `currency` expected ("$" for SEK, "kr" for COP) is never that currency."""
    foreign: set[str] = set()
    for line in (*page.lines(), *page.nodes()):
        for m in find_prices(line, [currency]):
            if _in_currency(m, currency, fx):
                return []
            foreign.add(m.currency if m.currency and m.currency != currency else _marker(m))
    return sorted(foreign)


def _names(bundle: Bundle) -> list[str]:
    return [n for n in (normalize_for_match(x) for x in (*bundle.aliases, bundle.name)) if n]


class _Budget:
    def __init__(self, max_pages: int) -> None:
        self.max = max(0, max_pages)
        self.taken: set[str] = set()
        self.skipped: list[str] = []

    def take(self, url: str) -> bool:
        if url in self.taken:
            return True
        if len(self.taken) >= self.max:
            if url not in self.skipped:
                self.skipped.append(url)
            return False
        self.taken.add(url)
        return True


@dataclass
class _Slot:
    """One page of a bundle while the watcher works on it."""

    url: str
    kind: PageKind
    watch_page: WatchPage | None
    prev: PageCheck | None
    # fetched | paused | over_budget | superseded | pending
    state: str = "pending"
    future: Awaitable[Page] | None = None
    page: Page | None = None
    status: PageStatus | None = None
    detail: str = ""
    # Versioned PDFs.
    edition: str | None = None
    newest_edition: str | None = None
    newest_url: str | None = None
    newest_future: Awaitable[Page] | None = None
    newest_page: Page | None = None
    newest_status: PageStatus | None = None
    index_current: bool = False
    edition_note: str = ""

    @property
    def versioned(self) -> bool:
        wp = self.watch_page
        return wp is not None and wp.index_url is not None and wp.link_pattern is not None


@dataclass
class _Job:
    bundle: Bundle
    provider: Provider
    prev: BundleWatchState | None
    slots: list[_Slot] = field(default_factory=list)

    def slot(self, url: str) -> _Slot | None:
        return next((s for s in self.slots if s.url == url), None)


def _slots(bundle: Bundle, prev: BundleWatchState | None) -> list[_Slot]:
    """Watch pages in order, each versioned PDF preceded by its index page (once)."""
    page_urls = {str(p.url) for p in bundle.watch.pages}
    out: list[_Slot] = []
    seen: set[str] = set()

    def add(url: str, kind: PageKind, wp: WatchPage | None) -> None:
        if url not in seen:
            seen.add(url)
            out.append(_Slot(url, kind, wp, prev.page(url) if prev else None))

    for wp in bundle.watch.pages:
        if wp.index_url is not None and str(wp.index_url) not in page_urls:
            add(str(wp.index_url), "index", None)
        add(str(wp.url), wp.kind, wp)
    return out


def _not_watched(bundle: Bundle, provider: Provider | None) -> tuple[WatchOutcome, str] | None:
    if provider is None:
        return "off", f"unknown provider {bundle.provider_id}"
    if provider.automated_access == "forbidden":
        return "off", "the provider's terms forbid automated access"
    if bundle.watch.mode != "auto":
        return bundle.watch.mode, f"watch.mode {bundle.watch.mode}"
    if not bundle.watch.pages:
        return "off", "no watch pages"
    return None


def _fresh_anchor(anchor: Anchor, **kw: Any) -> AnchorCheck:
    return AnchorCheck(
        anchor_hash=anchor_hash(anchor),
        fact=anchor.fact,
        service_id=anchor.service_id,
        url=str(anchor.url),
        **kw,
    )


def _carried(
    bundle: Bundle, prev: BundleWatchState | None, outcome: WatchOutcome, detail: str
) -> BundleWatchState:
    anchors = []
    for a in bundle.watch.anchors:
        old = prev.anchor(anchor_hash(a)) if prev else None
        anchors.append(old or _fresh_anchor(a, status="not_checked", detail=detail))
    return BundleWatchState(
        bundle_id=bundle.bundle_id,
        outcome=outcome,
        detail=detail,
        pages=list(prev.pages) if prev else [],
        anchors=anchors,
        hints=list(prev.hints) if prev else [],
        checked_at=prev.checked_at if prev else None,
    )


def page_status(
    page: Page, bundle: Bundle, provider: Provider, fx: FxTable | None = None
) -> tuple[PageStatus, str]:
    if page.status is None:
        final = page.final_url or page.url
        # A sign-in / account-verification page or a bot challenge that answered 200 is a wall:
        # unreadable, never the page's content (its anchors would all read as missing).
        if (wall := page.wall()) is not None:
            return "blocked", _clip(f"{wall} wall at {final}")
        if not on_domains(final, provider.domains.get(bundle.country, [])):
            return "wrong_country", _clip(
                f"final URL {final} is not on a {bundle.country} domain of {provider.id}"
            )
        foreign = foreign_currencies(page, bundle.price.currency, fx)
        if foreign:
            return "wrong_country", (
                f"prices in {', '.join(foreign)}, none in {bundle.price.currency}"
            )
        return "ok", ""
    if page.status == "unsupported_content" and page.is_pdf:
        return "pdf_error", _clip(page.detail)
    return _FETCH_STATUS.get(page.status, "failed"), _clip(page.detail)


# ------------------------------------------------------------------ value checks


def _component_value(bundle: Bundle, service_id: str | None) -> str | None:
    comp = next((c for c in bundle.components if c.service_id == service_id), None)
    if comp is None:
        return None
    if comp.inclusion in _COUNTED_INCLUSIONS and comp.value is not None:
        return f"{comp.service_id}:{comp.value}"
    return comp.service_id + (f".{comp.plan_id}" if comp.plan_id else "")


@dataclass(frozen=True)
class _Env:
    bundles: tuple[Bundle, ...]
    catalog: Catalog | None
    fx: FxTable | None
    today: date


def _anchor_value(anchor: Anchor, job: _Job, page: Page, env: _Env) -> ValueCheck | None:
    """The value-quote check of an anchor found on `page`, or None when it has nothing to check
    (validity anchors, an observed price, a component the YAML no longer lists)."""
    b = job.bundle
    # The page is searched with the text on one line; the check keeps the anchor's line breaks,
    # which end a clause for values.py (as in catalog's offline anchor check).
    flat = " ".join(anchor.text.split())
    text = anchor_lines(anchor.text)
    located = page.locate(flat)
    context = located[1] if located else ()
    if anchor.fact in _PRICE_FACTS:
        kind, attr = _PRICE_FACTS[anchor.fact]
        amount = getattr(b.price, attr)
        if amount is None:
            return None
        return check_value(
            kind,
            amount,
            text,
            context_lines=context,
            country=b.country,
            currency=b.price.currency,
            period=b.price.period,
            provider=job.provider,
            bundle=b,
            bundles=env.bundles,
            catalog=env.catalog,
            url=page.final_url or page.url,
        )
    if anchor.fact == "component":
        value = _component_value(b, anchor.service_id)
        if value is None:
            return None
        return check_value(
            "component",
            value,
            text,
            context_lines=context,
            country=b.country,
            currency=b.price.currency,
            bundle=b,
            bundles=env.bundles,
            catalog=env.catalog,
        )
    return None


def _check_anchor(anchor: Anchor, job: _Job, env: _Env) -> AnchorCheck:
    digest = anchor_hash(anchor)
    prev = job.prev.anchor(digest) if job.prev else None
    slot = job.slot(str(anchor.url))
    today = env.today
    carry: dict[str, Any] = {
        "last_verified_at": prev.last_verified_at if prev else None,
        "misses": prev.misses if prev else 0,
        "edition": slot.edition if slot else None,
        # An earlier miss stands until a read verifies the anchor again.
        "unconfirmed": prev.unconfirmed if prev else None,
        "unconfirmed_on": prev.unconfirmed_on if prev else None,
    }

    def undone(status: UnconfirmedStatus, **kw: Any) -> AnchorCheck:
        fields = {**carry, **kw, "unconfirmed": status, "unconfirmed_on": today}
        return _fresh_anchor(anchor, status=status, checked_at=today, **fields)

    if slot is None:
        return _fresh_anchor(anchor, status="not_checked", detail="not a watch page", **carry)
    if slot.state == "paused":
        return prev or _fresh_anchor(anchor, status="not_checked", detail="page paused", **carry)
    if slot.state == "over_budget":
        return _fresh_anchor(
            anchor, status="not_checked", detail="page budget spent", checked_at=today, **carry
        )
    if slot.state == "superseded":
        return undone(
            "outdated_document", detail=f"edition {slot.newest_edition} replaces {slot.edition}"
        )
    page = slot.page
    if slot.status != "ok" or page is None:
        detail = _clip(f"{slot.status}: {slot.detail}" if slot.detail else str(slot.status))
        return _fresh_anchor(anchor, status="unreadable", detail=detail, checked_at=today, **carry)
    via = page.contains(anchor.text, allow_embedded=True)
    misses = carry["misses"] + 1
    if via is None:
        return undone("missing", detail="not on the page", misses=misses)
    if via == "embedded":
        return undone("embedded_only", via=via, detail="only in the page's embedded data", misses=0)
    result = _anchor_value(anchor, job, page, env)
    if result is not None and not result.ok:
        detail = _clip(f"{result.status}: {result.reason}")
        return undone("missing", via=via, detail=detail, misses=misses)
    return _fresh_anchor(
        anchor,
        status="verified",
        via=via,
        checked_at=today,
        **{
            **carry,
            "last_verified_at": today,
            "misses": 0,
            "unconfirmed": None,
            "unconfirmed_on": None,
        },
    )


# ------------------------------------------------------------------ hints


def _hint_line(line: str, raw: str) -> str:
    if len(line) <= HINT_LINE_CHARS:
        return line
    at = max(0, line.find(raw) - 60)
    return line[at : at + HINT_LINE_CHARS]


def _offline_check(kind: ValueKind, amount: Decimal, job: _Job, env: _Env) -> Callable[[str], bool]:
    """validate-catalog's check of an anchor text for `amount` (no page context, no FX)."""
    b = job.bundle

    def accepts(text: str) -> bool:
        return check_value(
            kind,
            amount,
            anchor_lines(text),
            country=b.country,
            currency=b.price.currency,
            period=b.price.period,
            provider=job.provider,
            bundle=b,
            bundles=env.bundles,
            catalog=env.catalog,
        ).ok

    return accepts


def _hints(job: _Job, env: _Env) -> list[PriceHint]:
    b = job.bundle
    p = b.price
    currency = p.currency
    roles: list[tuple[str, ValueKind, Decimal | None, set[Decimal]]] = []
    if p.source != "observed":
        skip = {x for x in (p.amount, p.base_plan_amount, p.marginal_amount) if x is not None}
        roles.append(("regular", "money", p.amount, skip))
    roles.append(("intro", "intro_money", p.intro_amount, {p.intro_amount} - {None}))
    names = _names(b)
    sources: list[Page] = []
    for slot in job.slots:
        if slot.status == "ok" and slot.page is not None:
            sources.append(slot.page)
        if slot.newest_status == "ok" and slot.newest_page is not None:
            sources.append(slot.newest_page)
    out: list[PriceHint] = []
    seen: set[tuple[str, Decimal]] = set()
    # A hint found again keeps the day it was first found.
    first_seen = {
        (h.role, h.amount): h.first_seen
        for h in (job.prev.hints if job.prev else [])
        if h.currency == currency
    }
    for page in sources:
        lines = page.lines()
        url = canonical_url(page.final_url or page.url)
        for i, line in enumerate(lines):
            found = [m for m in find_prices(line, [currency], words=True) if m.currency == currency]
            if not found:
                continue
            context = lines[max(0, i - CONTEXT_BEFORE) : i + 1 + CONTEXT_AFTER]
            # A price list's columns are not understood: the row itself names the bundle.
            if page.is_pdf and not any(n in normalize_for_match(line) for n in names):
                continue
            for money in found:
                for role, kind, current, skip in roles:
                    if money.amount in skip or (role, money.amount) in seen:
                        continue
                    result = check_value(
                        kind,
                        money.amount,
                        line,
                        context_lines=context,
                        country=b.country,
                        currency=currency,
                        period=p.period,
                        provider=job.provider,
                        bundle=b,
                        bundles=env.bundles,
                        catalog=env.catalog,
                        fx=env.fx,
                        current=current,
                        url=page.final_url or page.url,
                    )
                    if not result.ok:
                        continue
                    seen.add((role, money.amount))
                    offline = _offline_check(kind, money.amount, job, env)
                    out.append(
                        PriceHint(
                            url=url,
                            amount=money.amount,
                            currency=currency,
                            period=p.period,
                            role=role,  # type: ignore[arg-type]
                            line=_hint_line(line, money.raw),
                            reason=_clip(result.reason),
                            big_change=result.big_change,
                            first_seen=first_seen.get((role, money.amount)) or env.today,
                            # The line alone may name no tier (a heading above names it):
                            # validate-catalog then needs the lines from that heading on.
                            anchor_text=anchor_span(page, line, offline),
                        )
                    )
                    break
                if len(out) >= MAX_HINTS:
                    return out
    return out


# ------------------------------------------------------------------ pages


def _edition_check(slot: _Slot, anchors: list[AnchorCheck]) -> EditionCheck | None:
    """How a versioned PDF that is not superseded is known to be the current edition."""
    if not slot.versioned:
        return None
    if slot.index_current:
        return "index"
    if slot.status == "ok" and any(
        a.fact == "validity" and a.url == slot.url and a.status == "verified" for a in anchors
    ):
        return "validity"
    return "unchecked"


def _page_check(slot: _Slot, job: _Job, anchors: list[AnchorCheck], today: date) -> PageCheck:
    prev = slot.prev
    if slot.state == "paused" and prev is not None:
        return prev
    common: dict[str, Any] = {
        "url": slot.url,
        "kind": slot.kind,
        "edition": slot.edition,
        "newest_edition": slot.newest_edition,
        "newest_url": slot.newest_url,
        "newest_status": slot.newest_status,
        "checked_at": today,
        "last_ok_at": prev.last_ok_at if prev else None,
        "unreadable_runs": prev.unreadable_runs if prev else 0,
    }
    if slot.state == "over_budget":
        return PageCheck(
            status="over_budget",
            detail="page budget spent",
            edition_check=_edition_check(slot, anchors),
            **common,
        )
    if slot.state == "superseded":
        newest = [
            a.anchor_hash
            for a, src in zip(anchors, job.bundle.watch.anchors, strict=True)
            if a.url == slot.url
            and slot.newest_page is not None
            and slot.newest_status == "ok"
            and slot.newest_page.contains(src.text)
        ]
        detail = f"edition {slot.newest_edition} replaces {slot.edition}"
        return PageCheck(
            status="superseded",
            detail=_clip(f"{detail} ({slot.edition_note})" if slot.edition_note else detail),
            newest_anchors=newest,
            **common,
        )
    page = slot.page
    assert page is not None and slot.status is not None
    edition_check = _edition_check(slot, anchors)
    detail = slot.detail
    if slot.status == "ok" and edition_check in ("validity", "unchecked"):
        detail = slot.edition_note
    ok = slot.status == "ok"
    runs = 0 if ok else common["unreadable_runs"] + 1
    common |= {
        "unreadable_runs": runs,
        "last_ok_at": today if ok else common["last_ok_at"],
    }
    return PageCheck(
        final_url=page.final_url,
        status=slot.status,
        http=page.http_status,
        content_type=page.content_type,
        detail=_clip(detail),
        page_hash=page.page_hash,
        edition_check=edition_check,
        paused_until=today + timedelta(days=PAUSE_DAYS) if runs >= PAUSE_AFTER_UNREADABLE else None,
        **common,
    )


# ------------------------------------------------------------------ entry point


async def watch_bundles(
    bundles: BundlesFile,
    *,
    pages: PageCache,
    previous: WatchFile | None,
    today: date,
    now: datetime | None = None,
    run_id: str | None = None,
    catalog: Catalog | None = None,
    fx: FxTable | None = None,
    max_pages: int = MAX_PAGES,
) -> WatchFile:
    """The watcher's pass over `bundles` (see the module docstring); `previous` is the last
    run's WatchFile (pauses, misses, last verification dates)."""
    budget = _Budget(max_pages)
    prev_by_id = {s.bundle_id: s for s in previous.bundles} if previous else {}
    states: dict[str, BundleWatchState] = {}
    jobs: list[_Job] = []
    for b in bundles.bundles:
        prev = prev_by_id.get(b.bundle_id)
        if is_expired(b, today):
            if prev is not None:
                states[b.bundle_id] = prev
            continue
        provider = bundles.provider(b.provider_id)
        skip = _not_watched(b, provider)
        if skip is not None:
            states[b.bundle_id] = _carried(b, prev, *skip)
            continue
        assert provider is not None
        jobs.append(_Job(b, provider, prev, _slots(b, prev)))

    def request(slot: _Slot, url: str) -> Awaitable[Page] | None:
        if slot.prev is not None and slot.prev.paused(today):
            slot.state = "paused"
            return None
        if not budget.take(url):
            slot.state = "over_budget"
            return None
        slot.state = "fetched"
        return pages.get(url)

    # 1. Every watch page and index page (the versioned documents wait for their index).
    for job in jobs:
        for slot in job.slots:
            if not slot.versioned:
                slot.future = request(slot, slot.url)
    await _settle(job for job in jobs)

    # 2. The document each versioned PDF points to.
    for job in jobs:
        domains = job.provider.domains.get(job.bundle.country, [])
        for slot in job.slots:
            if slot.versioned:
                _plan_versioned(slot, job, domains, budget, request, pages, fx)
    await _settle(job for job in jobs)

    # 3. Anchors, hints, page checks and the outcome.
    env = _Env(tuple(bundles.bundles), catalog, fx, today)
    for job in jobs:
        for slot in job.slots:
            if slot.state == "fetched" and slot.page is not None:
                slot.status, slot.detail = page_status(slot.page, job.bundle, job.provider, fx)
            if slot.newest_page is not None:
                slot.newest_status = page_status(slot.newest_page, job.bundle, job.provider, fx)[0]
        anchors = [_check_anchor(a, job, env) for a in job.bundle.watch.anchors]
        checks = [_page_check(slot, job, anchors, today) for slot in job.slots]
        anchors = _current_editions_only(anchors, checks, job)
        state = BundleWatchState(
            bundle_id=job.bundle.bundle_id,
            outcome=_outcome(job, anchors),
            pages=checks,
            anchors=anchors,
            hints=_hints(job, env),
            checked_at=today
            if any(s.state == "fetched" for s in job.slots)
            else (job.prev.checked_at if job.prev else None),
        )
        states[job.bundle.bundle_id] = state
        log.info("bundle watch %s: %s", state.bundle_id, state.outcome)
    if budget.skipped:
        log.warning(
            "bundle watch: page budget of %d spent; not fetched: %s",
            budget.max,
            ", ".join(budget.skipped),
        )
    return WatchFile(
        run_id=run_id,
        generated_at=now or datetime.now(UTC),
        bundles=[states[b.bundle_id] for b in bundles.bundles if b.bundle_id in states],
    )


async def _settle(jobs: Iterable[_Job]) -> None:
    for job in jobs:
        for slot in job.slots:
            if slot.future is not None and slot.page is None:
                slot.page = await slot.future
            if slot.newest_future is not None and slot.newest_page is None:
                slot.newest_page = await slot.newest_future


def _plan_versioned(
    slot: _Slot,
    job: _Job,
    domains: Sequence[str],
    budget: _Budget,
    request: Any,
    pages: PageCache,
    fx: FxTable | None,
) -> None:
    wp = slot.watch_page
    assert wp is not None and wp.index_url is not None and wp.link_pattern is not None
    pattern = re.compile(wp.link_pattern)
    slot.edition = _edition_of(pattern, slot.url)
    index = job.slot(str(wp.index_url))
    index_page = index.page if index is not None else None
    readable = (
        index_page is not None
        and index is not None
        and index.state == "fetched"
        and page_status(index_page, job.bundle, job.provider, fx)[0] == "ok"
    )
    links = edition_links(index_page, pattern, domains) if readable and index_page else {}
    newest: str | None = None
    newest_url: str | None = None
    if links:
        newest = max(links, key=edition_key)
        newest_url = links[newest]
    else:
        slot.edition_note = (
            "the index links no edition of this document"
            if readable
            else "the index could not be read"
        )
        prev = slot.prev
        if prev is not None and prev.newest_edition and prev.newest_url:
            # Editions only go up: a newer one an earlier run found is not undone by an index
            # that cannot be read (or lists nothing) this run.
            newest, newest_url = prev.newest_edition, prev.newest_url
    if (
        newest is not None
        and newest_url is not None
        and slot.edition is not None
        and edition_key(newest) > edition_key(slot.edition)
    ):
        slot.newest_edition, slot.newest_url = newest, newest_url
        slot.state = "superseded"
        if not links:
            slot.edition_note += f"; edition {newest} was found by an earlier run"
        if budget.take(newest_url):
            slot.newest_future = pages.get(newest_url)
        else:
            slot.newest_status = "over_budget"
        return
    target = slot.url
    if links and newest is not None and newest_url is not None:
        slot.newest_edition, slot.newest_url = newest, newest_url
        slot.index_current = newest == slot.edition
        if not slot.index_current:
            slot.edition_note = (
                f"the index links no edition as new as {slot.edition} (newest {newest})"
            )
        elif newest_url != canonical_url(slot.url):
            # The same edition at another URL (a re-upload): read it where the index links it.
            target = newest_url
    slot.future = request(slot, target)


def _current_editions_only(
    anchors: list[AnchorCheck], checks: list[PageCheck], job: _Job
) -> list[AnchorCheck]:
    """An excerpt found in a versioned PDF that is not known to be the current edition
    (edition_check "unchecked") confirms nothing: it keeps its previous last_verified_at and
    unconfirmed state."""
    unchecked = {c.url for c in checks if c.edition_check == "unchecked"}
    out: list[AnchorCheck] = []
    for a in anchors:
        if a.status == "verified" and a.url in unchecked:
            prev = job.prev.anchor(a.anchor_hash) if job.prev else None
            a = a.model_copy(
                update={
                    "last_verified_at": prev.last_verified_at if prev else None,
                    "unconfirmed": prev.unconfirmed if prev else None,
                    "unconfirmed_on": prev.unconfirmed_on if prev else None,
                    "detail": "found, but the document is not known to be the current edition",
                }
            )
        out.append(a)
    return out


def _outcome(job: _Job, anchors: list[AnchorCheck]) -> WatchOutcome:
    if any(a.status == "missing" and a.fact in DECISIVE_FACTS for a in anchors):
        return "changed"
    if any(s.state == "superseded" for s in job.slots):
        return "new_document"
    # A versioned PDF's index page is only read for its links: it counts as a page read only
    # when an anchor quotes it.
    quoted = {str(a.url) for a in job.bundle.watch.anchors}
    if not any(s.status == "ok" and (s.kind != "index" or s.url in quoted) for s in job.slots):
        return "unreadable"
    return "ok"


def pages_over_budget(state: BundleWatchState, on: date | None = None) -> list[str]:
    """URLs of a bundle's pages the page budget left out (a watch or index page, or the newest
    edition of a superseded PDF), in the checks dated `on` (any date when None)."""
    out: list[str] = []
    for p in state.pages:
        if on is not None and p.checked_at != on:
            continue
        if p.status == "over_budget":
            out.append(p.url)
        if p.newest_status == "over_budget" and p.newest_url:
            out.append(p.newest_url)
    return out


def summary(watch: WatchFile) -> dict[str, Any]:
    """Counts for the run manifest's report (research/latest/run.json)."""
    watched = [b for b in watch.bundles if b.outcome not in ("manual", "off")]
    return {
        "bundles": len(watch.bundles),
        "outcomes": dict(sorted(Counter(b.outcome for b in watch.bundles).items())),
        "anchors_verified": sum(
            1 for b in watch.bundles for a in b.anchors if a.status == "verified"
        ),
        "hints": sum(len(b.hints) for b in watch.bundles),
        "pages_over_budget": sum(len(pages_over_budget(b)) for b in watched),
    }
