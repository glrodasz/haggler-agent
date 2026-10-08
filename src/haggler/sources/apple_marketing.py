"""Apple One, Apple Music and Apple TV prices from apple.com marketing pages.

apple.com/{apple_path}/{page}/ is server-rendered HTML with localized plan names ("Individuell",
"Familiar", "Premier") and prices such as "195 kr/mån.", "$19.95/mo." or "$ 16.900/mes".
Strategies, in order: schema.org Offers in ld+json, then plan-label/price pairing over the
visible text (single-plan services such as Apple TV take the most repeated regular monthly
price). Apple serves unknown storefronts the US page, so the final URL and canonical link must
keep the country's path segment and prices must be in the country's currency.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, CountryDef, ServiceDef
from haggler.models.observations import BillingPeriod, ObservationDraft
from haggler.models.run import Task
from haggler.money import Money, find_prices, parse_bare_amount
from haggler.sources.base import ParseResult, PerCountryAdapter, draft, match_plan
from haggler.sources.heuristics import detect_period, plan_price_pairs

FLAGS = ["apple_billing"]

# Other products these pages advertise with their own prices (marker -> id/class slug).
_FOREIGN: dict[str, str] = {
    "apple one": r"apple-?one",
    "season pass": r"season-?pass",
    "mls": r"(?<![a-z])mls(?![a-z])",
    "peacock": r"peacock",
}
_SAVINGS = re.compile(
    r"(?<!\w)(save|saving|savings|ahorr\w*|spara|sparar|spar|besparing|sparen|économis\w*"
    r"|economiz\w*|risparmi\w*|oszczęd\w*|ušetř\w*|tasarruf\w*|separately|por separado"
    r"|separat|einzeln|value of|valor de)(?!\w)",
    re.IGNORECASE,
)
_INSTALLMENTS = re.compile(
    r"installment|instalment|cuotas|meses sin intereses|delbetal|avbetal|ratenzahlung"
    r"|apple card|financ",
    re.IGNORECASE,
)
# Apple's sharing feature names contain plan aliases ("Family", "familia") but are not plans.
_FAMILY_SHARING = re.compile(
    r"family\s+sharing|familjedelning|en\s+familia|compartir\s+en\s+familia|partage\s+familial"
    r"|familienfreigabe|familiedeling|perhejako|aile\s+paylaşımı",
    re.IGNORECASE,
)
# A price right after these words is the regular one ("Free for 1 month, then $10.99/mo.").
_THEN = re.compile(
    r"(then|after(wards)?|thereafter|därefter|sedan|luego|despu[ée]s|danach|anschließend"
    r"|ensuite|puis|derefter|deretter|sonra|potem|depois|daarna|poi|jälkeen|potom|apoi)"
    r"\W*(\w+\W+){0,2}$",
    re.IGNORECASE,
)
_INTRO_WORDS = re.compile(
    r"free|gratis|gratuit|kostenlos|ücretsiz|trial|prueba|provperiod|prøve|essai|first|primer"
    r"|första|intro|special|oferta|erbjudande|offer|angebot|promo",
    re.IGNORECASE,
)
_N_MONTHS = (
    r"\d+\s*(months?|mo\b|mån\w*|meses|mes\b|monate?n?|mois|weeks?|veckor|semanas|days?"
    r"|dagar|días|dias)"
)
# "$2.99/mo. for 3 months", "for 12 mo." (installments) right after the price.
_INTRO_AFTER = re.compile(
    rf"^\W*(\w+\W+){{0,3}}?(for|för|por|durante|für|pendant|i|en|per)\s+(the\s+)?"
    rf"(first\s+|primeros?\s+|första\s+|ersten\s+)?{_N_MONTHS}",
    re.IGNORECASE,
)
# "Get 3 months of Apple TV for $2.99/mo." right before the price.
_INTRO_BEFORE = re.compile(
    rf"{_N_MONTHS}\s+(of\s+\w+(\s+\w+)?\s+)?(for|för|por|für|pour|på)\s*$", re.IGNORECASE
)
_BLOCKED = re.compile(r"access denied|you don't have permission to access|request unsuccessful")
_STOREFRONT = re.compile(r"[a-z]{2}|[a-z]{2}(de|fr|it|nl|en)|lae")

_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "template", "iframe", "head", "-comment"}
)
_BLOCK_TAG_NAMES = (
    "address article aside blockquote caption dd details dialog div dl dt fieldset "
    "figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr li main nav ol p "
    "pre section summary table tbody td tfoot th thead tr ul button option "
)
_BLOCK_TAGS = frozenset(_BLOCK_TAG_NAMES.split())
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_WS = re.compile(r"[\s\u200b]+")


@dataclass(frozen=True)
class _Line:
    text: str
    heading: int  # 1-6 when the text sits in a heading element, else 0
    context: str  # lower-cased ids/classes of the enclosing elements (inside <body>)


def _page_lines(root: LexborNode | None) -> list[_Line]:
    """Visible text, one line per block element, with inline markup joined.

    Unlike haggler.text.html_to_lines (one line per text node) this keeps
    "<span>195 kr</span>/mån." together and remembers headings and section classes, which the
    upsell filter needs. Footnote marks in <sup> are dropped.
    """
    lines: list[_Line] = []
    buf: list[str] = []
    stack: list[tuple[int, str]] = []  # (heading level, ids/classes) of open elements
    # Heading level and context of the first visible text in `buf` (None = nothing yet).
    meta: tuple[int, str] | None = None

    def flush() -> None:
        nonlocal meta
        text = _WS.sub(" ", "".join(buf)).strip()
        buf.clear()
        if text and meta is not None:
            lines.append(_Line(text, *meta))
        meta = None

    def add(text: str) -> None:
        nonlocal meta
        if meta is None and text.strip():
            level = next((lvl for lvl, _ in reversed(stack) if lvl), 0)
            meta = (level, " ".join(ident for _, ident in stack if ident).lower())
        buf.append(text)

    def walk(node: LexborNode) -> None:
        for child in node.iter(include_text=True):
            if child.is_text_node:
                add(child.text_content or "")
                continue
            tag = child.tag or ""
            if tag in _SKIP_TAGS or child.is_comment_node:
                continue
            if tag == "br":
                flush()
                continue
            attrs = child.attributes
            ident = " ".join(
                v
                for k in ("id", "class", "data-analytics-section-engagement")
                if (v := attrs.get(k))
            )
            stack.append((_HEADINGS.get(tag, 0), ident))
            if tag == "sup":
                # Footnote marks are dropped: "$5.99<sup>3</sup>/mo." must not read "5.99 3".
                if len((child.text(deep=True) or "").strip()) > 4:
                    add(" ")
                    walk(child)
                    add(" ")
            else:
                is_block = tag in _BLOCK_TAGS
                if is_block:
                    flush()
                walk(child)
                if is_block:
                    flush()
            stack.pop()

    if root is not None:
        walk(root)
        flush()
    return lines


def _without_foreign(lines: list[_Line], svc: ServiceDef, expected: list[str]) -> list[_Line]:
    """Drop upsell sections for other products (Apple One on the Apple Music page, ...).

    A heading naming another product hides everything up to the next heading of the same or
    higher level; sections whose id/class names it are hidden; so is any priced line naming it.
    """
    own = svc.name.casefold()
    markers = [m for m in _FOREIGN if m not in own]
    if not markers:
        return lines
    word = re.compile("|".join(rf"(?<!\w){re.escape(m)}(?!\w)" for m in markers), re.I)
    slug = re.compile("|".join(_FOREIGN[m] for m in markers), re.I)
    out: list[_Line] = []
    skip_level = 0
    for line in lines:
        if line.heading and skip_level and line.heading <= skip_level:
            skip_level = 0
        if line.heading and word.search(line.text):
            skip_level = line.heading
            continue
        if skip_level or slug.search(line.context):
            continue
        if word.search(line.text) and find_prices(line.text, expected):
            continue
        out.append(line)
    return out


def _plans_named(svc: ServiceDef, text: str) -> set[str]:
    norm = " ".join(text.casefold().split())
    return {
        p.id
        for p in svc.plans
        for a in p.aliases
        if re.search(rf"(?<![\w]){re.escape(a.casefold())}(?![\w])", norm)
    }


def _clean_texts(lines: list[_Line], svc: ServiceDef, expected: list[str]) -> list[str]:
    """Lines that could mislead label/price pairing are removed: savings and installment
    claims, "Family Sharing" mentions and plan enumerations without a price."""
    out: list[str] = []
    for line in lines:
        text = line.text
        priced = bool(find_prices(text, expected))
        if priced and (_SAVINGS.search(text) or _INSTALLMENTS.search(text)):
            continue
        if not priced and (_FAMILY_SHARING.search(text) or len(_plans_named(svc, text)) > 1):
            continue
        out.append(text)
    return out


def _is_intro(line: str, money: Money) -> bool:
    prefix = line[max(0, money.start - 60) : money.start]
    if _THEN.search(prefix):
        return False
    if _INTRO_AFTER.search(line[money.end : money.end + 50]) or _INTRO_BEFORE.search(prefix):
        return True
    clause = re.split(r"[.!?;•|]\s", prefix)[-1]
    return bool(_INTRO_WORDS.search(clause))


def _single_plan_price(texts: list[str], expected: list[str]) -> tuple[Money, str] | None:
    """The regular monthly price of a one-plan service: the most repeated non-introductory
    monthly amount (Apple repeats it in hero, fine print and footnotes); ties -> first seen."""
    candidates: list[tuple[Money, str]] = []
    for i, text in enumerate(texts):
        prices = find_prices(text, expected)
        for k, money in enumerate(prices):
            if money.amount <= 0 or money.currency not in expected:
                continue
            # The period must follow this price, not the next one ("$129 or $10.75/mo.").
            stop = prices[k + 1].start if k + 1 < len(prices) else len(text)
            tail = text[money.end : min(stop, money.end + 30)]
            if not tail.strip() and i + 1 < len(texts):
                tail = texts[i + 1][:20]  # "<p>99 kr</p><p>/mån.</p>"
            if detect_period(tail) != "month":
                continue
            if not _is_intro(text, money):
                candidates.append((money, text))
    if not candidates:
        return None
    counts = Counter((m.amount, m.currency) for m, _ in candidates)
    best = max(counts.values())
    return next(c for c in candidates if counts[(c[0].amount, c[0].currency)] == best)


def _types(obj: dict[str, Any]) -> set[str]:
    t = obj.get("@type")
    if isinstance(t, str):
        return {t}
    return {x for x in t if isinstance(x, str)} if isinstance(t, list) else set()


def _collect_offers(obj: Any, parent_name: str, out: list[tuple[dict[str, Any], str]]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _collect_offers(item, parent_name, out)
        return
    if not isinstance(obj, dict):
        return
    name = obj["name"] if isinstance(obj.get("name"), str) else parent_name
    if "Offer" in _types(obj):
        out.append((obj, parent_name))
    for value in obj.values():
        _collect_offers(value, name, out)


def _ld_amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return Decimal(str(value))
    return parse_bare_amount(str(value))


_ISO_PERIOD: dict[str, BillingPeriod] = {
    "P1M": "month",
    "P3M": "quarter",
    "P1Y": "year",
    "P12M": "year",
}
_UNIT_PERIOD: dict[str, BillingPeriod] = {"MON": "month", "ANN": "year", "QAN": "quarter"}


def _ld_period(offer: dict[str, Any], label: str) -> BillingPeriod:
    specs = offer.get("priceSpecification") or []
    for spec in specs if isinstance(specs, list) else [specs]:
        if not isinstance(spec, dict):
            continue
        duration = str(spec.get("billingDuration") or "").upper()
        if duration in _ISO_PERIOD:
            return _ISO_PERIOD[duration]
        ref = spec.get("referenceQuantity")
        unit = str(spec.get("unitCode") or (ref.get("unitCode") if isinstance(ref, dict) else ""))
        if unit.upper() in _UNIT_PERIOD:
            return _UNIT_PERIOD[unit.upper()]
    text = " ".join(str(offer.get(k) or "") for k in ("name", "description")) + " " + label
    return detect_period(text) or "month"


@dataclass
class _LdOffer:
    label: str
    amount: Decimal
    currency: str
    period: BillingPeriod


def _ld_offers(tree: LexborHTMLParser) -> list[_LdOffer]:
    raw: list[tuple[dict[str, Any], str]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            _collect_offers(json.loads(node.text() or ""), "", raw)
        except ValueError:
            continue
    out: list[_LdOffer] = []
    for offer, parent_name in raw:
        item = offer.get("itemOffered")
        item_name = item.get("name") if isinstance(item, dict) else None
        label = str(offer.get("name") or item_name or parent_name or "")
        specs = offer.get("priceSpecification") or []
        specs = [s for s in (specs if isinstance(specs, list) else [specs]) if isinstance(s, dict)]
        price = offer.get("price", next((s.get("price") for s in specs if "price" in s), None))
        currency = offer.get("priceCurrency") or next(
            (s.get("priceCurrency") for s in specs if s.get("priceCurrency")), None
        )
        amount = _ld_amount(price)
        if amount is None or amount <= 0 or not isinstance(currency, str):
            continue
        out.append(_LdOffer(label, amount, currency.upper(), _ld_period(offer, label)))
    return out


def _is_storefront(segment: str, cat: Catalog) -> bool:
    paths = {c.apple_path.lower() for c in cat.countries if c.apple_path}
    return segment in paths or bool(_STOREFRONT.fullmatch(segment))


def _storefront_issue(url: str, country: CountryDef, cat: Catalog) -> str | None:
    """Why `url` is not this country's apple.com storefront (None when it is)."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host and host != "apple.com" and not host.endswith(".apple.com"):
        return f"left apple.com: {url}"
    segments = [s.lower() for s in parts.path.split("/") if s]
    path = (country.apple_path or "").lower()
    if path and (not segments or segments[0] != path):
        return f"expected the /{path}/ storefront, got {parts.path or '/'}"
    if not path and segments and segments[0] != "us" and _is_storefront(segments[0], cat):
        return f"expected the US storefront, got /{segments[0]}/"
    return None


def _looks_blocked(resp: RawResponse) -> bool:
    """Akamai-style denial page (Apple's CDN): tiny body saying access is denied."""
    if resp.status_code == 403:
        return True
    return len(resp.text) < 8000 and bool(_BLOCKED.search(resp.text.casefold()))


def _lang_note(tree: LexborHTMLParser, country: CountryDef) -> str | None:
    node = tree.css_first("html")
    lang = (node.attributes.get("lang") or "") if node else ""
    m = re.fullmatch(r"[a-zA-Z]{2,3}[-_]([a-zA-Z]{2})", lang.strip())
    if m and m.group(1).upper() != country.cc:
        return f"html lang {lang} does not match {country.cc}"
    return None


def _response_problem(
    resp: RawResponse, task: Task, country: CountryDef, cat: Catalog
) -> ParseResult | None:
    """Bot wall, missing page or another storefront: no point in reading prices."""
    if _looks_blocked(resp):
        return ParseResult(status="blocked", notes=[f"bot wall (HTTP {resp.status_code})"])
    if resp.status_code in (404, 410):
        return ParseResult(status="not_available", notes=[f"HTTP {resp.status_code}"])
    final_url = resp.final_url or resp.url or task.url
    if issue := _storefront_issue(final_url, country, cat):
        return ParseResult(status="wrong_country", notes=[issue])
    segments = [s for s in urlsplit(final_url).path.split("/") if s]
    if len(segments) <= (1 if country.apple_path else 0):
        # Sent to the storefront's home page: this product page does not exist there.
        return ParseResult(status="not_available", notes=[f"redirected to {final_url}"])
    return None


def _canonical_problem(tree: LexborHTMLParser, country: CountryDef, cat: Catalog) -> str | None:
    """Replayed pages keep the requested URL, so the canonical link is a second check."""
    node = tree.css_first('link[rel="canonical"]')
    href = (node.attributes.get("href") or "") if node else ""
    if href.startswith("http") and (issue := _storefront_issue(href, country, cat)):
        return f"canonical: {issue}"
    return None


class AppleMarketingAdapter(PerCountryAdapter):
    id = "apple_marketing"
    requires = ("apple_path",)

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        svc = cat.service(task.service_ids[0])
        country = cat.country(task.countries[0])
        if problem := _response_problem(resp, task, country, cat):
            return problem
        tree = LexborHTMLParser(resp.text)
        if issue := _canonical_problem(tree, country, cat):
            return ParseResult(status="wrong_country", notes=[issue])

        notes: list[str] = []
        if note := _lang_note(tree, country):
            notes.append(note)
        expected = list(country.expected_currencies(task.channel))
        if country.appstore_currency and country.appstore_currency not in expected:
            expected.append(country.appstore_currency)

        foreign: Counter[str] = Counter()
        found = self._from_ld_json(task, svc, tree, expected, foreign)
        if found:
            notes.append(f"ld+json offers: {', '.join(sorted(found))}")

        lines = _without_foreign(_page_lines(tree.body), svc, expected)
        texts = _clean_texts(lines, svc, expected)
        for d in self._from_text(task, svc, texts, expected):
            prev = found.setdefault(d.plan_id, d)
            if prev is not d and (prev.amount, prev.currency) != (d.amount, d.currency):
                notes.append(f"{d.plan_id}: ld+json {prev.amount} vs text {d.amount}")

        local = False
        for text in texts:
            for m in find_prices(text, expected):
                if m.amount > 0 and m.currency in expected:
                    local = True
                elif m.amount > 0:
                    foreign[m.currency or "?"] += 1

        rank = {p.id: i for i, p in enumerate(svc.plans)}
        observations = sorted(found.values(), key=lambda d: rank.get(d.plan_id, 99))
        missing = [p.id for p in svc.plans if p.id not in found]
        if svc.default_plan in found:
            status = "ok"
        elif found:
            status = "partial"
        elif foreign and not local:
            status = "wrong_country"
            notes.append(
                f"prices shown in {', '.join(sorted(foreign))}; expected {', '.join(expected)}"
            )
        else:
            status = "no_prices"
        return ParseResult(
            observations=observations, status=status, missing_plans=missing, notes=notes
        )

    def _from_ld_json(
        self,
        task: Task,
        svc: ServiceDef,
        tree: LexborHTMLParser,
        expected: list[str],
        foreign: Counter[str],
    ) -> dict[str, ObservationDraft]:
        found: dict[str, ObservationDraft] = {}
        for offer in _ld_offers(tree):
            if offer.currency not in expected:
                foreign[offer.currency] += 1
                continue
            plan_id = svc.default_plan if len(svc.plans) == 1 else match_plan(svc, offer.label)
            if plan_id is None or plan_id in found:
                continue
            found[plan_id] = draft(
                task,
                plan_id=plan_id,
                label=offer.label or svc.name,
                amount=offer.amount,
                currency=offer.currency,
                period=offer.period,
                evidence=f"ld+json Offer: {offer.label} {offer.amount} {offer.currency}",
                flags=list(FLAGS),
            )
        return found

    def _from_text(
        self, task: Task, svc: ServiceDef, texts: list[str], expected: list[str]
    ) -> list[ObservationDraft]:
        if len(svc.plans) == 1:
            hit = _single_plan_price(texts, expected)
            if hit is None:
                return []
            money, line = hit
            return [
                draft(
                    task,
                    plan_id=svc.default_plan,
                    label=svc.name,
                    amount=money.amount,
                    currency=money.currency or expected[0],
                    period="month",
                    evidence=line,
                    flags=list(FLAGS),
                )
            ]
        return [
            draft(
                task,
                plan_id=pp.plan_id,
                label=pp.label,
                amount=pp.money.amount,
                currency=pp.money.currency or expected[0],
                period=pp.period,
                evidence=pp.evidence,
                flags=list(FLAGS),
            )
            for pp in plan_price_pairs(texts, svc, expected, window=8)
        ]
