"""Spotify Premium prices from spotify.com/{market}/premium/.

The page is server-rendered (Next.js). Plan data is read from embedded JSON when present
(`__NEXT_DATA__` or other JSON scripts) and otherwise from the visible plan cards ("Premium
Individual" ... "129 kr/månad"). Intro offers ("0 kr i 1 månad", "€0 for 1 month", "3 meses por
COP 9.250") are dropped so only the regular monthly price is recorded. Tiers outside the catalog
(Premium Lite / Standard / Platinum, Basic) are ignored. The market path segment is the country
code unless params.path_overrides names another one (the UK market is /uk/, not /gb/).

The second half of this module holds page helpers that are generic across marketing-page
adapters (disney.py and netflix_help.py import them): label/price strategies, embedded JSON and
the choice between strategies. Line rendering lives in haggler.text and price classification
(intro offers, billing periods) in haggler.sources.heuristics; `page_lines`, `is_intro` and
`price_period` are re-exported here for older callers.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, CountryDef, ServiceDef
from haggler.models.observations import BillingPeriod
from haggler.models.run import Task
from haggler.money import ZERO_DECIMAL, Money, find_prices, parse_bare_amount
from haggler.sources.base import (
    ParseResult,
    ParseStatus,
    PerCountryAdapter,
    draft,
    market_segment,
    match_plan,
)
from haggler.sources.heuristics import (
    PlanPrice,
    is_intro,
    label_period,
    lends_period,
    plan_price_pairs,
    price_period,
    price_tail,
)
from haggler.text import html_to_lines, page_title
from haggler.text import node_lines as node_lines  # re-exported for older callers

# Tiers sold in some markets that are not catalog plans ("Premium Lite", "Basic Individual").
_OTHER_TIER = re.compile(r"\b(lite|platinum|standard|basic|mini)\b", re.IGNORECASE)
# Internal plan identifiers that may appear in embedded JSON ("premium-family", "duo").
_PLAN_ID = re.compile(
    r"^(?:spotify[-_.]?)?(?:premium[-_.]?)?(individual|duo|family|student)"
    r"(?:[-_.]?(?:premium|plan|monthly))?$",
    re.IGNORECASE,
)


def _other_tier_label(line: str) -> bool:
    return len(line) <= 40 and bool(_OTHER_TIER.search(line))


def _plan_from_id(value: str) -> str | None:
    m = _PLAN_ID.match(value.strip())
    return m.group(1).lower() if m else None


def _market_mismatch(url: str, cc: str, market: str | None = None) -> str | None:
    """The market segment of a final URL when it is not the requested one (/us/, /int/...).

    `market` is the segment the request used when it is not the country code (GB -> "uk").
    """
    parts = [p for p in urlsplit(url).path.lower().split("/") if p]
    if not parts:
        return None
    seg = parts[0]
    if seg in {"int", "intl"}:
        return seg
    accepted = {cc.lower(), (market or cc).lower()}
    if re.fullmatch(r"[a-z]{2}(?:-[a-z]{2,4})?", seg) and seg.split("-")[0] not in accepted:
        return seg
    return None


class SpotifyPremiumAdapter(PerCountryAdapter):
    id: ClassVar[str] = "spotify_premium"

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        svc = cat.service(task.service_ids[0])
        country = cat.country(task.countries[0])
        expected = expected_currencies(task, country)
        final = resp.final_url or resp.url
        market = market_segment(task.params, country.cc)
        seg = _market_mismatch(final, country.cc, market)
        if seg:
            return ParseResult(
                status="wrong_country",
                notes=[f"final URL is market /{seg}/, not /{market}/: {final}"],
            )
        if resp.status_code in (404, 410):
            return ParseResult(
                status="not_available", notes=[f"HTTP {resp.status_code}: no premium page"]
            )

        html = resp.text
        lines = page_lines(html)
        docs = json_documents(html)

        def run(cur: list[str]) -> list[tuple[str, list[PlanHit]]]:
            return [
                (
                    "json",
                    json_plan_hits(
                        docs,
                        svc,
                        cur,
                        reject=lambda s: bool(_OTHER_TIER.search(s)),
                        id_matcher=_plan_from_id,
                    ),
                ),
                ("cards", card_hits(lines, svc, cur, reject=_other_tier_label)),
            ]

        return resolve(task, svc, resp, lines, expected, run)


# =============================================================================================
# Generic page helpers (shared with disney.py / netflix_help.py)
# =============================================================================================


@dataclass
class PlanHit:
    """One plan price found by one strategy, before deciding which strategy to trust."""

    plan_id: str
    label: str
    amount: Decimal
    currency: str | None
    evidence: str
    strategy: str
    period: BillingPeriod = "month"
    intro: bool = False


def expected_currencies(task: Task, country: CountryDef) -> list[str]:
    """Catalog currencies, unless the source pins others (params.currency_overrides: {RO: EUR})."""
    pinned = (task.params.get("currency_overrides") or {}).get(country.cc)
    if pinned:
        return [pinned] if isinstance(pinned, str) else list(pinned)
    return country.expected_currencies(task.channel)


# ---- HTML -> rendered lines (haggler.text) -------------------------------------------------


def page_lines(html: str, *, drop_chrome: bool = True) -> list[str]:
    """Rendered lines of the page body, without header/nav/footer (menus list plan names)."""
    return html_to_lines(html, drop_chrome=drop_chrome)


# ---- page state ---------------------------------------------------------------------------

_BLOCKED = re.compile(
    r"captcha|are you a (?:robot|human)|verify (?:that )?you(?:'re| are) (?:a )?human"
    r"|access denied|pardon our interruption|request (?:was )?blocked|unusual traffic"
    r"|just a moment\.\.\.|attention required|bot detection|please enable cookies"
    r"|checking your browser",
    re.IGNORECASE,
)
_CHALLENGE_MARKERS = (
    "px-captcha",
    "cf-chl-",
    "cf_chl_",
    "_Incapsula_Resource",
    "captcha-delivery.com",
    "g-recaptcha",
    "h-captcha",
)
_UNAVAILABLE = re.compile(
    r"(?:\bnot|n't|\bär\s+inte|\bno\s+está|\bnão\s+está|\bn'est\s+pas|\bist\s+nicht"
    r"|\bnie\s+jest|\bikke)\s+(?:yet\s+|currently\s+|ännu\s+|todavía\s+|aún\s+)?"
    r"(?:available|tillgänglig\w*|disponible|disponível|verfügbar|dostępn\w*|tilgængelig"
    r"|tilgjengelig)\s+(?:in|i|en|em|dans|im|w|på)\s+"
    r"(?:(?:your|this|ditt|din|tu|su|seu|sua|votre|deinem|deiner|ihrem|twoim|dit)\s+)?"
    r"(?:country|region|location|area|land|landet|område|regionen|país|región|região|pays"
    r"|région|kraju|regionie)",
    re.IGNORECASE,
)


def looks_blocked(resp: RawResponse, lines: list[str]) -> bool:
    if resp.status_code in (403, 429):
        return True
    head = " ".join([page_title(resp.text), *lines[:60]])
    if _BLOCKED.search(head):
        return True
    return len(lines) < 80 and any(m in resp.text for m in _CHALLENGE_MARKERS)


def unavailable_text(lines: list[str]) -> str | None:
    for line in lines:
        if _UNAVAILABLE.search(line):
            return line[:160]
    return None


# ---- price classification -----------------------------------------------------------------

# A period marker right after a price ("/år", " per year") cut together with a yearly price.
_PERIOD_TOKEN = re.compile(
    r"^\s*(?:/\s*[^\s\d/]+\.?|(?:per|a|om|al|por|pro|par|i|na|w)\s+[^\s\d]+)", re.IGNORECASE
)


def currency_ok(amount: Decimal, currency: str | None, expected: list[str]) -> bool:
    if currency not in expected:
        return False
    # "$7.99" read as COP on a page that fell back to USD.
    return not (currency in ZERO_DECIMAL and amount != amount.to_integral_value())


def clean_price_lines(lines: list[str], expected: list[str]) -> list[str]:
    """Blank out prices that must never be taken as the regular monthly price.

    Zero, intro and yearly prices are removed (with their "/år" marker; a yearly price may also
    be labelled before it, "Annual: ₹1,189") so plan_price_pairs only sees regular monthly or
    unmarked prices, whatever order the page lists them in.
    """
    out: list[str] = []
    for i, line in enumerate(lines):
        found = find_prices(line, expected)
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        cuts: list[tuple[int, int]] = []
        prev_end = 0
        for k, m in enumerate(found):
            nxt = found[k + 1].start if k + 1 < len(found) else len(line)
            before, prev_end = line[prev_end : m.start], m.end
            tail = price_tail(line, m.end, nxt, 30)
            if not tail.strip() and lends_period(next_line, expected):
                tail = next_line  # "<span>1 090 kr</span><div>/år</div>"
            if m.amount <= 0 or is_intro(line[: m.start], line[m.end :]):
                cuts.append((m.start, m.end))
            elif (label_period(before) or price_period(tail)) in ("year", "quarter"):
                tok = _PERIOD_TOKEN.match(line[m.end : nxt])
                cuts.append((m.start, m.end + (tok.end() if tok else 0)))
        for start, end in reversed(cuts):
            line = f"{line[:start]} {line[end:]}"
        out.append(line.strip())
    return out


# ---- strategies ---------------------------------------------------------------------------


def _label_like(line: str) -> bool:
    return len(line) <= 48 and not re.search(r"[:;,!?]", line)


def header_runs(
    lines: list[str], svc: ServiceDef, plan_of: Callable[[str], str | None] | None = None
) -> list[tuple[int, int]]:
    """[start, end) ranges of >=2 consecutive short lines naming distinct plans."""
    plan_of = plan_of or (lambda s: match_plan(svc, s))
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        seen: set[str] = set()
        j = i
        while j < len(lines) and _label_like(lines[j]):
            pid = plan_of(lines[j])
            if pid is None or pid in seen:
                break
            seen.add(pid)
            j += 1
        if j - i >= 2:
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def monthly_price(text: str, expected: list[str], next_text: str = "") -> Money | None:
    """First regular monthly-or-unmarked price in a line or table cell."""
    found = find_prices(text, expected)
    prev_end = 0
    for k, m in enumerate(found):
        before, prev_end = text[prev_end : m.start], m.end
        if m.amount <= 0 or is_intro(text[: m.start], text[m.end :]):
            continue
        nxt = found[k + 1].start if k + 1 < len(found) else len(text)
        tail = price_tail(text, m.end, nxt, 30)
        if not tail.strip() and lends_period(next_text, expected):
            tail = next_text  # "<span>1 090 kr</span><span>/år</span>" rendered on two lines
        if (label_period(before) or price_period(tail)) in ("year", "quarter"):
            continue
        return m
    return None


@dataclass
class ColumnRun:
    start: int
    end: int
    plans: list[tuple[str, str]]  # (plan_id, label line) in column order
    prices: list[tuple[Money, str]]  # (price, its line) in the same order


def column_runs(
    lines: list[str],
    svc: ServiceDef,
    expected: list[str],
    plan_of: Callable[[str], str | None] | None = None,
) -> list[ColumnRun]:
    """Comparison grids: N consecutive plan names followed by exactly N monthly prices.

    Counting must match before another plan name appears, so cards (name, price, CTA naming the
    plan, next name ...) are not mistaken for a grid even when a CTA sits above the next title.
    """
    plan_of = plan_of or (lambda s: match_plan(svc, s))
    out: list[ColumnRun] = []
    for start, end in header_runs(lines, svc, plan_of):
        plans = [(plan_of(lines[k]) or "", lines[k]) for k in range(start, end)]
        prices: list[tuple[Money, str]] = []
        k = end
        while k < min(len(lines), end + 6 * len(plans)) and len(prices) <= len(plans):
            if len(lines[k]) <= 60 and plan_of(lines[k]):
                break
            money = monthly_price(lines[k], expected, lines[k + 1] if k + 1 < len(lines) else "")
            if money:
                prices.append((money, lines[k]))
            k += 1
        if len(prices) == len(plans):
            out.append(ColumnRun(start, end, plans, prices))
    return out


def card_hits(
    lines: list[str],
    svc: ServiceDef,
    expected: list[str],
    *,
    reject: Callable[[str], bool] | None = None,
    window: int = 6,
) -> list[PlanHit]:
    """plan_price_pairs over cleaned lines.

    Lines for which `reject` is true (tiers or bundles we must ignore) are dropped and split the
    page, so a plan's window never runs into their card. Grid headers (see column_runs) are
    blanked: plan_price_pairs would give the last header the first column's price.
    """
    cleaned = clean_price_lines(lines, expected)
    for run in column_runs(lines, svc, expected):
        for k in range(run.start, run.end):
            cleaned[k] = ""
    segments: list[list[int]] = [[]]
    for k, line in enumerate(cleaned):
        if reject and reject(line):
            segments.append([])
        else:
            segments[-1].append(k)
    hits: list[PlanHit] = []
    for idxs in segments:
        seg = [cleaned[k] for k in idxs]
        # clean_price_lines already removed intro prices with the number-based rule; the
        # offer-word rule is tuned on Apple pages, so it stays off for these cards.
        for pp in plan_price_pairs(seg, svc, expected, window=window, intro_words=False):
            hits.append(
                PlanHit(
                    plan_id=pp.plan_id,
                    label=pp.label,
                    amount=pp.money.amount,
                    currency=pp.money.currency,
                    evidence=_original_evidence(seg, [lines[k] for k in idxs], pp) or pp.evidence,
                    strategy="cards",
                )
            )
    return hits


def _original_evidence(seg: list[str], orig: list[str], pp: PlanPrice) -> str | None:
    """The page's own lines from the plan label to its price (cleaned lines lack intro prices)."""
    for i, line in enumerate(seg):
        if line != pp.label:
            continue
        for j in range(i, min(len(seg), i + 8)):
            if pp.money.raw in seg[j]:
                return " ".join(orig[i : j + 1])[-240:]
    return None


# ---- embedded JSON ------------------------------------------------------------------------

_ASSIGN = re.compile(
    r"^\s*(?:(?:var|let|const)\s+)?(?:window\.|self\.|globalThis\.)?[\w$]+"
    r"(?:\.[\w$]+|\[\s*['\"][\w$-]+['\"]\s*\])*\s*=\s*(?=[\[{])"
)
_JS_TYPES = frozenset({"", "text/javascript", "application/javascript", "module"})


def json_documents(html: str) -> list[tuple[str, Any]]:
    """(identifier, parsed JSON) for JSON scripts and `window.X = {...}` state assignments."""
    docs: list[tuple[str, Any]] = []
    decoder = json.JSONDecoder(parse_float=Decimal)
    for node in LexborHTMLParser(html).css("script"):
        typ = (node.attributes.get("type") or "").lower().strip()
        ident = node.attributes.get("id") or typ or "script"
        body = (node.text() or "").strip()
        if not body or len(body) > 8_000_000:
            continue
        if typ in {"application/json", "application/ld+json"} or ident == "__NEXT_DATA__":
            with contextlib.suppress(json.JSONDecodeError):
                docs.append((ident, json.loads(body, parse_float=Decimal)))
            continue
        if typ not in _JS_TYPES:
            continue
        m = _ASSIGN.match(body)
        if not m:
            continue
        try:
            obj, _ = decoder.raw_decode(body, m.end())
        except json.JSONDecodeError:
            continue
        docs.append((body[: m.end()].rstrip(" =") if ident == "script" else ident, obj))
    return docs


_LABEL_KEYS = frozenset(
    {
        "name", "title", "heading", "header", "headline", "planname", "plan_name", "productname",
        "product_name", "displayname", "display_name", "label", "tier", "tiername", "plantitle",
        "offername", "plandisplayname",
    }
)  # fmt: skip
_ID_KEYS = frozenset(
    {
        "id", "planid", "plan_id", "plan", "productid", "product_id", "producttype", "sku",
        "slug", "key", "code", "tierid", "offerid", "type", "plantype", "plancode",
    }
)  # fmt: skip
_CURRENCY_KEYS = frozenset(
    {"currency", "currencycode", "currency_code", "pricecurrency", "isocurrency", "currencyiso"}
)
_PERIOD_KEYS = frozenset(
    {
        "period", "billingperiod", "billing_period", "interval", "billinginterval",
        "billingcycle", "duration", "frequency", "recurrence", "term", "cadence", "periodunit",
        "billingfrequency",
    }
)  # fmt: skip
_PRICE_KEY = re.compile(r"price|amount|cost|pricing|(?:^|_)fee", re.IGNORECASE)
# Keys that mention a price but hold identifiers ("priceId", "priceType", "amountKey").
_NOT_PRICE_KEY = re.compile(r"(?:id|ids|key|type|code|count|index|url|uri|link|tier)$", re.I)
_GENERIC_VALUE_KEYS = frozenset(
    {
        "value", "formatted", "formattedvalue", "display", "displayvalue", "text", "label",
        "localized", "string", "raw", "rawvalue", "total", "monthly", "annual", "yearly",
        "month", "year",
    }
)  # fmt: skip
_INTRO_KEY = re.compile(r"intro|trial|promo|discount|teaser|first|special|initial", re.I)
_YEAR_KEY = re.compile(r"annual|year|p1y|12m", re.IGNORECASE)
_MONTH_KEY = re.compile(r"month|p1m", re.IGNORECASE)


def _period_value(value: Any) -> BillingPeriod | None:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if v in {"p1y", "y", "1y", "12m", "p12m"} or re.search(r"year|annual|anual|år", v):
        return "year"
    if v in {"p1m", "m", "1m"} or re.search(r"month|mes|mån|monat", v):
        return "month"
    if v in {"p3m", "3m"} or "quarter" in v:
        return "quarter"
    return None


def _json_plan(
    node: dict[str, Any],
    svc: ServiceDef,
    reject: Callable[[str], bool] | None,
    id_matcher: Callable[[str], str | None] | None,
) -> tuple[str, str] | None:
    for k, v in node.items():
        if k.lower() in _LABEL_KEYS and isinstance(v, str) and 0 < len(v) <= 80:
            if reject and reject(v):
                return None
            pid = match_plan(svc, v)
            if pid:
                return pid, v
    if id_matcher:
        plan_ids = {p.id for p in svc.plans}
        for k, v in node.items():
            if k.lower() in _ID_KEYS and isinstance(v, str):
                pid = id_matcher(v)
                if pid in plan_ids:
                    return pid, v
    return None


def _number(v: Any) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, (int, Decimal)):
        return None
    return Decimal(v) if isinstance(v, int) else v


@dataclass
class _Cand:
    amount: Decimal
    currency: str | None
    period: BillingPeriod
    intro: bool
    text: str
    formatted: bool = True


def _str_cands(
    v: str, expected: list[str], currency: str | None, period: BillingPeriod | None, intro: bool
) -> list[_Cand]:
    if len(v) > 300:
        return []
    found = find_prices(v, expected)
    out = []
    for i, m in enumerate(found):
        nxt = found[i + 1].start if i + 1 < len(found) else len(v)
        out.append(
            _Cand(
                amount=m.amount,
                currency=m.currency,
                period=price_period(v[m.end : min(nxt, m.end + 30)]) or period or "month",
                intro=intro or is_intro(v[: m.start], v[m.end :]),
                text=v,
            )
        )
    if not found and currency and re.fullmatch(r"\s*\d[\d.,\s]*\s*", v):
        amount = parse_bare_amount(v)  # {"price": "129.00", "priceCurrency": "SEK"}
        if amount is not None:
            out.append(_Cand(amount, currency, period or "month", intro, v))
    return out


def _json_prices(
    node: dict[str, Any],
    expected: list[str],
    *,
    in_price: bool,
    currency: str | None,
    period: BillingPeriod | None,
    intro: bool,
    depth: int,
    is_plan: Callable[[dict[str, Any]], bool],
) -> list[_Cand]:
    for k, v in node.items():
        if k.lower() in _CURRENCY_KEYS and isinstance(v, str) and re.fullmatch(r"[A-Za-z]{3}", v):
            currency = v.upper()
        elif k.lower() in _PERIOD_KEYS:
            period = _period_value(v) or period
    out: list[_Cand] = []
    for k, v in node.items():
        kl = k.lower()
        priceish = (bool(_PRICE_KEY.search(kl)) or (in_price and kl in _GENERIC_VALUE_KEYS)) and (
            not _NOT_PRICE_KEY.search(kl)
        )
        k_intro = intro or bool(_INTRO_KEY.search(kl))
        k_period: BillingPeriod | None = (
            "year" if _YEAR_KEY.search(kl) else "month" if _MONTH_KEY.search(kl) else period
        )
        if isinstance(v, str):
            if priceish:
                out += _str_cands(v, expected, currency, k_period, k_intro)
        elif (num := _number(v)) is not None:
            if priceish and currency:
                if "micros" in kl:
                    num = num / 1_000_000
                elif "minor" in kl or "cents" in kl:
                    num = num / 100
                out.append(_Cand(num, currency, k_period or "month", k_intro, f"{k}={v}", False))
        elif depth < 3 and isinstance(v, (dict, list)):
            for child in v if isinstance(v, list) else [v]:
                if isinstance(child, str) and priceish:
                    out += _str_cands(child, expected, currency, k_period, k_intro)
                elif isinstance(child, dict) and not is_plan(child):
                    out += _json_prices(
                        child,
                        expected,
                        in_price=in_price or priceish,
                        currency=currency,
                        period=k_period,
                        intro=k_intro,
                        depth=depth + 1,
                        is_plan=is_plan,
                    )
    return out


def json_plan_hits(
    docs: Iterable[tuple[str, Any]],
    svc: ServiceDef,
    expected: list[str],
    *,
    reject: Callable[[str], bool] | None = None,
    id_matcher: Callable[[str], str | None] | None = None,
) -> list[PlanHit]:
    """Prices of JSON objects that name a plan ("name"/"title"/... or an id) and carry a price.

    Prices are read from the object itself and from nested price objects (not from nested
    objects that name another plan): formatted strings ("129 kr/mån"), or numbers next to a
    currency key ({"amount": 129, "currency": "SEK"}).
    """
    hits: list[PlanHit] = []

    def is_plan(d: dict[str, Any]) -> bool:
        return _json_plan(d, svc, reject, id_matcher) is not None

    def visit(node: Any, depth: int) -> None:
        if depth > 60:
            return
        if isinstance(node, dict):
            found = _json_plan(node, svc, reject, id_matcher)
            if found:
                pid, label = found
                cands = _json_prices(
                    node,
                    expected,
                    in_price=False,
                    currency=None,
                    period=None,
                    intro=False,
                    depth=0,
                    is_plan=is_plan,
                )
                # Formatted strings first: they are what the page shows (and quote better).
                for c in sorted(cands, key=lambda c: not c.formatted):
                    hits.append(
                        PlanHit(
                            plan_id=pid,
                            label=label,
                            amount=c.amount,
                            currency=c.currency,
                            evidence=f"{label}: {c.text}"[:240],
                            strategy="json",
                            period=c.period,
                            intro=c.intro,
                        )
                    )
            for v in node.values():
                if isinstance(v, (dict, list)):
                    visit(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                visit(v, depth + 1)

    for _, doc in docs:
        visit(doc, 0)
    return hits


# ---- choosing and reporting ---------------------------------------------------------------


def pick_hits(
    hits: Iterable[PlanHit], expected: list[str]
) -> tuple[dict[str, PlanHit], list[PlanHit], list[str]]:
    """First regular monthly price per plan in an expected currency; other-currency hits."""
    best: dict[str, PlanHit] = {}
    foreign: list[PlanHit] = []
    notes: list[str] = []
    for h in hits:
        if h.amount <= 0 or h.period != "month" or h.intro:
            continue
        if not currency_ok(h.amount, h.currency, expected):
            foreign.append(h)
            continue
        cur = best.get(h.plan_id)
        if cur is None:
            best[h.plan_id] = h
        elif cur.amount != h.amount and len(notes) < 5:
            notes.append(f"{h.strategy}: {h.plan_id} also at {h.amount} (kept {cur.amount})")
    return best, foreign, notes


def choose(
    results: list[tuple[str, dict[str, PlanHit]]], default_plan: str
) -> tuple[str | None, dict[str, PlanHit], list[str]]:
    """Trust one strategy: the first that has the default plan, else the one with most plans.

    Strategies are never mixed: a column-layout page read as cards would mis-assign prices,
    and mixing would hide that. Disagreements end up in the notes for the probe logs.
    """
    best_name: str | None = None
    best: dict[str, PlanHit] = {}
    best_score = (False, 0)
    for name, hits in results:
        score = (default_plan in hits, len(hits))
        if hits and score > best_score:
            best_name, best, best_score = name, hits, score
    notes = []
    for name, hits in results:
        if hits:
            summary = " ".join(f"{pid}={h.amount}{h.currency or ''}" for pid, h in hits.items())
            notes.append(f"{name}: {summary}")
        if name == best_name:
            continue
        for pid, h in hits.items():
            if pid in best and best[pid].amount != h.amount:
                notes.append(f"{name} disagrees on {pid}: {h.amount} vs {best[pid].amount}")
    if best_name:
        notes.insert(0, f"strategy={best_name}")
    return best_name, best, notes


def hits_result(
    task: Task, svc: ServiceDef, chosen: dict[str, PlanHit], notes: list[str]
) -> ParseResult:
    order = {p.id: i for i, p in enumerate(svc.plans)}
    observations = [
        draft(
            task,
            plan_id=h.plan_id,
            label=h.label,
            amount=h.amount,
            currency=h.currency or "",
            evidence=h.evidence,
        )
        for h in sorted(chosen.values(), key=lambda h: order.get(h.plan_id, 99))
    ]
    status: ParseStatus = "ok" if svc.default_plan in chosen else "partial"
    missing = [p.id for p in svc.plans if p.id not in chosen]
    return ParseResult(observations=observations, status=status, missing_plans=missing, notes=notes)


def empty_status(
    resp: RawResponse, lines: list[str], foreign: list[PlanHit]
) -> tuple[ParseStatus, list[str]]:
    """Why a page yielded no usable price."""
    if looks_blocked(resp, lines):
        return "blocked", [f"bot wall or challenge page (HTTP {resp.status_code})"]
    text = unavailable_text(lines)
    if text:
        return "not_available", [f"page says: {text}"]
    if foreign:
        seen = sorted({h.currency or f"unresolved '{h.evidence[-40:]}'" for h in foreign})
        return "wrong_country", [f"plan prices only in other currencies: {', '.join(seen)}"]
    return "no_prices", []


def resolve(
    task: Task,
    svc: ServiceDef,
    resp: RawResponse,
    lines: list[str],
    expected: list[str],
    run: Callable[[list[str]], list[tuple[str, list[PlanHit]]]],
) -> ParseResult:
    """Run strategies (`run(currencies)` -> [(name, hits)]), pick one, build the ParseResult.

    When nothing matches the expected currencies the strategies run again accepting any
    currency: plan prices in another currency mean the page fell back to another country.
    """
    picked = []
    notes: list[str] = []
    foreign: list[PlanHit] = []
    for name, raw in run(expected):
        best, other, more = pick_hits(raw, expected)
        picked.append((name, best))
        foreign += other
        notes += more
    _, chosen, choice_notes = choose(picked, svc.default_plan)
    if chosen:
        return hits_result(task, svc, chosen, choice_notes + notes)
    for _, raw in run([]):
        foreign += pick_hits(raw, expected)[1]
    status, why = empty_status(resp, lines, foreign)
    return ParseResult(status=status, notes=why + notes)
