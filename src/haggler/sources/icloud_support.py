"""iCloud+ prices for every country from Apple's support article (one static page).

support.apple.com/en-us/108047 ("iCloud+ plans and pricing", formerly HT201238) is one fetch for
all countries, so this adapter plans a single task and fans the page out into per-country
drafts. Two layouts are read:
  * tables (the 2026 article): one table per region, header row "Country (Currency) | 50 GB |
    200 GB | 2 TB | 6 TB | 12 TB", rows such as "Croatia³ (Euro) | 0.99 € | 2.99 € | ...";
  * lists (the older article): headers such as "Sweden (SEK)" or "Bahamas²³ (USD)" (several
    countries may share one header) followed by one line per tier ("50GB: $0.99").
The currency in parentheses is an ISO code or an English currency name ("Euro", "US Dollar",
babel's names in singular and plural). A row named after a currency instead of a country
("Euro³ (Euro)") covers every catalog country billed in that currency that has no row of its own
(the article lists Croatia separately and the other euro countries under "Euro").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from urllib.parse import urlsplit

from babel.numbers import get_currency_name, list_currencies
from selectolax.lexbor import LexborHTMLParser, LexborNode

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, CountryDef, ServiceDef, SourceDef
from haggler.models.observations import ObservationDraft
from haggler.models.run import Task
from haggler.money import find_prices, parse_bare_amount
from haggler.sources.base import ParseResult, draft, host_of, make_task_id
from haggler.text import node_lines

FLAGS = ["apple_billing"]
# Share of the task's countries that must be on the page for status "ok".
OK_RATIO = 0.8

# "50GB", "2 TB", "12TB" (not "1.5GB" or "250GB" glued to other digits).
_TIER = re.compile(r"(?<![\w.,])(\d{1,3})\s?(GB|TB)\b", re.IGNORECASE)
_FOOTNOTE_MARKS = "¹²³⁴⁵⁶⁷⁸⁹⁰*†‡§"
_FOOTNOTE = re.compile(rf"[\d{_FOOTNOTE_MARKS}]+")
# "Sweden (SEK)", "Bahamas 2 3 (USD)", "Estonia, Latvia, and Lithuania (EUR):", "Peru (PEN)¹",
# "Croatia³ (Euro)", "Ecuador (US Dollar)".
_HEADER = re.compile(
    rf"^(?P<names>[^()]*?[^\W\d_][^()]*?)\s*\(\s*(?P<cur>[^()]{{2,40}}?)\s*\)\s*:?"
    rf"\s*[\d\s{_FOOTNOTE_MARKS}]*$"
)
# Words around a currency used as a region name ("Euro", "Eurozone", "Other euro countries").
_REGION_WORDS = re.compile(r"\b(?:other|all)\s+|\s*(?:zone|area|countries|region|regions)$")
_NAME_SPLIT = re.compile(r"\s*(?:,|;|/|&|\band\b)\s*")
_BARE_NUMBER = re.compile(r"^\W*(\d[\d.,\s]*\d|\d)\W*$")
_BLOCKED = re.compile(r"access denied|you don't have permission to access|request unsuccessful")
# Spellings babel does not list.
_EXTRA_CURRENCY_NAMES = {"pound sterling": "GBP", "sterling": "GBP", "uk pound": "GBP"}


def _fold(text: str) -> str:
    """Lower-case ASCII words: "Türkiye  Lira" -> "turkiye lira", "Króna" -> "krona"."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).split())


@cache
def _iso_codes() -> frozenset[str]:
    return frozenset(list_currencies())


@cache
def _names_of(code: str) -> frozenset[str]:
    """Folded English names of a currency: display name, singular and plural."""
    names = set()
    for count in (None, 1, 2):
        try:
            names.add(_fold(get_currency_name(code, count=count, locale="en")))
        except (KeyError, ValueError):  # pragma: no cover - babel has every ISO code
            continue
    return frozenset(n for n in names if n and n != code.lower())


@cache
def _all_names() -> dict[str, str]:
    out: dict[str, str] = {}
    for code in sorted(_iso_codes()):
        for name in _names_of(code):
            out.setdefault(name, code)
    return out


def currency_code(text: str, cat: Catalog) -> str | None:
    """ISO code for "SEK", "Euro", "US Dollar", "Swedish kronor" ... (None when unknown).

    Names of the catalog's currencies win over historical ones that share a spelling.
    """
    raw = text.strip()
    if re.fullmatch(r"[A-Z]{3}", raw):
        return raw if raw in _iso_codes() else None
    key = _fold(raw)
    if not key:
        return None
    for code in sorted(cat.currencies()):
        if key in _names_of(code):
            return code
    return _EXTRA_CURRENCY_NAMES.get(key) or _all_names().get(key)


def _currency_region(names: str, cat: Catalog) -> str | None:
    """Currency named by a pseudo-country row ("Euro", "Eurozone", "Other euro countries")."""
    key = _REGION_WORDS.sub(" ", _fold(_clean_names(names))).strip()
    return currency_code(key, cat) if key else None


@dataclass
class _Entry:
    """One tier price for the countries named by one header."""

    countries: list[CountryDef]
    currency: str
    plan_id: str
    label: str
    amount: Decimal
    evidence: str
    # Set for a currency row ("Euro (Euro)"): it covers catalog countries in that currency
    # that have no row of their own.
    covers: str | None = None


@dataclass
class _Header:
    text: str
    countries: list[CountryDef]
    currency: str
    covers: str | None = None


def _tier_plan(svc: ServiceDef, size: str, unit: str) -> str | None:
    plan_id = f"{int(size)}{unit.lower()}"
    return plan_id if svc.plan(plan_id) else None


def _clean_names(names: str) -> str:
    """Drop footnote marks ("Bahamas²³", "Brazil 1") and any "Label:" prefix."""
    return " ".join(_FOOTNOTE.sub(" ", names.split(":")[-1]).split()).replace(" ,", ",")


def _countries_named(names: str, cat: Catalog) -> list[CountryDef]:
    """Catalog countries in a header's name part; unknown names (non-catalog) are ignored."""
    cleaned = _clean_names(names)
    whole = cat.country_by_name(cleaned)
    if whole:
        return [whole]
    found: list[CountryDef] = []
    for part in _NAME_SPLIT.split(cleaned):
        country = cat.country_by_name(part) if part else None
        if country and country not in found:
            found.append(country)
    return found


def _parse_header(text: str, cat: Catalog) -> _Header | None:
    """ "Sweden (SEK)", "Croatia³ (Euro)", "Euro³ (Euro)"; None unless the parentheses hold a
    currency."""
    text = text.strip()
    if len(text) > 600:
        return None
    m = _HEADER.match(text)
    if not m:
        return None
    currency = currency_code(m["cur"], cat)
    if currency is None:
        return None
    names = m.group("names")
    countries = _countries_named(names, cat)
    covers = None if countries else _currency_region(names, cat)
    return _Header(
        text=f"{_clean_names(names)} ({m['cur'].strip()})",
        countries=countries,
        currency=currency,
        covers=covers if covers == currency else None,
    )


def _evidence(header: _Header, tier_text: str) -> str:
    """Header + tier line, shortening long multi-country headers so the tier text survives the
    240-character evidence limit."""
    head = header.text
    budget = 236 - len(tier_text)
    if len(head) > budget:
        head = head[: max(budget - 8, 10)].rstrip(" ,") + f"… ({header.currency})"
    return f"{head} | {tier_text}"


def _segment_price(segment: str, currency: str) -> Decimal | None:
    """Price of one tier segment (": 129 kr").

    Bare numbers count because the header names the currency; a price whose marker cannot be
    that currency ("US$" or "kr" under "(USD)" resolves to another or to None) is rejected
    rather than relabelled.
    """
    prices = find_prices(segment, [currency])
    for money in prices:
        if money.amount > 0 and money.currency == currency:
            return money.amount
    m = _BARE_NUMBER.match(segment)
    if m and not prices:
        amount = parse_bare_amount(m.group(1))
        if amount and amount > 0:
            return amount
    return None


def _tier_entries(line: str, header: _Header, svc: ServiceDef) -> list[_Entry]:
    tokens = list(_TIER.finditer(line))
    out: list[_Entry] = []
    for k, tok in enumerate(tokens):
        end = tokens[k + 1].start() if k + 1 < len(tokens) else len(line)
        segment = line[tok.end() : end]
        plan_id = _tier_plan(svc, tok.group(1), tok.group(2))
        amount = _segment_price(segment, header.currency)
        if plan_id is None or amount is None:
            continue
        tier_text = line[tok.start() : end].strip(" ,;")
        out.append(
            _Entry(
                countries=header.countries,
                currency=header.currency,
                plan_id=plan_id,
                label=tok.group(0),
                amount=amount,
                evidence=_evidence(header, tier_text),
                covers=header.covers,
            )
        )
    return out


def _from_lines(lines: list[str], svc: ServiceDef, cat: Catalog, named: set[str]) -> list[_Entry]:
    """Walk the article: a header opens a country block, tier lines fill it.

    The block closes at the next header, at the first non-tier line after its tiers, or after
    two unrelated lines, so footnotes and region headings are never attributed to a country.
    Countries that get a header of their own are added to `named`.
    """
    entries: list[_Entry] = []
    header: _Header | None = None
    seen_tiers = False
    gap = 0
    for line in lines:
        first = _TIER.search(line)
        if first:
            inline = _parse_header(line[: first.start()], cat)
            if inline:
                header, seen_tiers, gap = inline, False, 0
                named.update(c.cc for c in inline.countries)
            if header:
                found = _tier_entries(line, header, svc)
                if found:
                    entries.extend(found)
                    seen_tiers, gap = True, 0
                    continue
        parsed = _parse_header(line, cat)
        if parsed:
            header, seen_tiers, gap = parsed, False, 0
            named.update(c.cc for c in parsed.countries)
            continue
        if header:
            gap += 1
            if seen_tiers or gap > 2:
                header = None
    return entries


def _cell_text(cell: LexborNode) -> str:
    return " ".join(node_lines(cell))


def _row_header(
    cells: list[str], currency_col: int | None, cat: Catalog, notes: list[str]
) -> _Header | None:
    """The country (or currency) a table row is for, and the currency of its cells."""
    header = _parse_header(cells[0], cat)
    if header is not None:
        return header
    names = cells[0].split("(")[0]  # "Sweden (Kronor)": a currency we cannot read
    countries = _countries_named(names, cat)
    if not countries:
        return None
    cur = None
    if currency_col not in (None, 0) and currency_col < len(cells):
        cur = currency_code(cells[currency_col], cat)
    if cur is None:
        cur = countries[0].currency
        if "(" in cells[0]:
            notes.append(f"currency of row {cells[0]!r} not recognised; assumed {cur}")
    return _Header(text=f"{_clean_names(names)} ({cur})", countries=countries, currency=cur)


def _from_tables(
    tree: LexborHTMLParser, svc: ServiceDef, cat: Catalog, named: set[str], notes: list[str]
) -> list[_Entry]:
    """Countries as rows and tiers as columns ("Country (Currency) | 50 GB | 200 GB | ...")."""
    entries: list[_Entry] = []
    for table in tree.css("table"):
        columns: list[str | None] | None = None
        currency_col: int | None = None
        for row in table.css("tr"):
            cells = [_cell_text(c) for c in row.css("th, td")]
            if not cells:
                continue
            if columns is None:
                plans = []
                for c in cells:  # "50GB", "iCloud+ 50 GB"
                    m = _TIER.search(c) if len(c) <= 24 else None
                    plans.append(_tier_plan(svc, m.group(1), m.group(2)) if m else None)
                if sum(p is not None for p in plans) >= 2:
                    columns = plans
                    currency_col = next(
                        (i for i, c in enumerate(cells) if re.search(r"currenc|moneda", c, re.I)),
                        None,
                    )
                continue
            header = _row_header(cells, currency_col, cat, notes)
            if header is None:
                continue
            named.update(c.cc for c in header.countries)
            for idx, plan_id in enumerate(columns):
                if plan_id is None or idx == 0 or idx >= len(cells):
                    continue
                amount = _segment_price(cells[idx], header.currency)
                if amount is None:
                    continue
                label = plan_id.upper().replace("GB", " GB").replace("TB", " TB")
                entries.append(
                    _Entry(
                        countries=header.countries,
                        currency=header.currency,
                        plan_id=plan_id,
                        label=label,
                        amount=amount,
                        evidence=_evidence(header, f"{label}: {cells[idx]}"),
                        covers=header.covers,
                    )
                )
    return entries


def _looks_blocked(resp: RawResponse) -> bool:
    """Akamai-style denial page (Apple's CDN): tiny body saying access is denied."""
    if resp.status_code == 403:
        return True
    return len(resp.text) < 8000 and bool(_BLOCKED.search(resp.text.casefold()))


class ICloudSupportAdapter:
    """One task for the whole world; parse fans out per country."""

    id = "icloud_support"

    def plan(self, svc: ServiceDef, src: SourceDef, cat: Catalog) -> list[Task]:
        url = src.params["url"]
        countries = [c.cc for c in cat.countries if src.applies_to(c.cc)]
        if not countries:
            return []
        return [
            Task(
                task_id=make_task_id(self.id, svc.id, "ALL"),
                adapter_id=self.id,
                service_ids=[svc.id],
                countries=countries,
                channel=src.channel,
                kind=src.kind,
                url=url,
                host=host_of(url),
                params=src.params,
            )
        ]

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        if _looks_blocked(resp):
            return ParseResult(status="blocked", notes=[f"bot wall (HTTP {resp.status_code})"])
        svc = cat.service(task.service_ids[0])
        notes: list[str] = []
        final_path = urlsplit(resp.final_url or resp.url or task.url).path.lower()
        if not final_path.startswith("/en-us/"):
            notes.append(f"final URL is not the en-us article: {resp.final_url}")

        tree = LexborHTMLParser(resp.text)
        named: set[str] = set()
        entries = _from_tables(tree, svc, cat, named, notes)
        entries += _from_lines(node_lines(tree.body), svc, cat, named)

        wanted = set(task.countries)
        values: dict[tuple[str, str], Decimal] = {}
        drafts: list[ObservationDraft] = []

        def add(e: _Entry, cc: str) -> None:
            key = (cc, e.plan_id)
            if key in values:
                if values[key] != e.amount:
                    notes.append(f"{cc} {e.plan_id}: kept {values[key]}, ignored {e.amount}")
                return
            values[key] = e.amount
            drafts.append(
                draft(
                    task,
                    plan_id=e.plan_id,
                    label=e.label,
                    amount=e.amount,
                    currency=e.currency,
                    evidence=e.evidence,
                    country=cc,
                    flags=list(FLAGS),
                )
            )

        for e in entries:
            for country in e.countries:
                if country.cc in wanted:
                    add(e, country.cc)
        # Currency rows ("Euro (Euro)") last: a country's own row always wins.
        covered: dict[str, list[str]] = {}
        for e in entries:
            if e.covers is None:
                continue
            for country in cat.countries:
                cc = country.cc
                if cc in wanted and cc not in named and country.currency == e.covers:
                    add(e, cc)
                    if cc not in covered.setdefault(e.covers, []):
                        covered[e.covers].append(cc)
        for cur, ccs in covered.items():
            notes.append(f"{cur} row applied to countries without their own row: {', '.join(ccs)}")

        order = {cc: i for i, cc in enumerate(task.countries)}
        rank = {p.id: i for i, p in enumerate(svc.plans)}
        drafts.sort(key=lambda d: (order.get(d.country, 999), rank.get(d.plan_id, 999)))

        found = {cc for cc, _ in values}
        missing = [cc for cc in task.countries if cc not in found]
        if missing:
            notes.append(f"countries not on page: {', '.join(missing)}")
        no_default = [cc for cc in sorted(found) if (cc, svc.default_plan) not in values]
        if no_default:
            notes.append(f"default plan {svc.default_plan} missing for: {', '.join(no_default)}")

        if not drafts:
            status = "no_prices"
        elif task.countries and len(found) / len(task.countries) >= OK_RATIO:
            status = "ok"
        else:
            status = "partial"
        return ParseResult(observations=drafts, status=status, notes=notes)
