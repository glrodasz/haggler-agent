"""iCloud+ prices for every country from Apple's support article (one static page).

support.apple.com/en-us/108047 ("iCloud+ plans and pricing", formerly HT201238) lists each
country or region as a header such as "Sweden (SEK)" or "Bahamas²³ (USD)" (several countries may
share one header), followed by one line per storage tier ("50GB: $0.99", "2 TB: 129 kr"). One
fetch covers all countries, so this adapter plans a single task and fans the page out into
per-country drafts. A table layout (countries as rows, tiers as columns) is parsed too, in case
Apple reformats the article.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, CountryDef, ServiceDef, SourceDef
from haggler.models.observations import ObservationDraft
from haggler.models.run import Task
from haggler.money import find_prices, parse_bare_amount
from haggler.sources.base import ParseResult, draft, host_of, make_task_id

FLAGS = ["apple_billing"]
# Share of the task's countries that must be on the page for status "ok".
OK_RATIO = 0.8

# "50GB", "2 TB", "12TB" (not "1.5GB" or "250GB" glued to other digits).
_TIER = re.compile(r"(?<![\w.,])(\d{1,3})\s?(GB|TB)\b", re.IGNORECASE)
_FOOTNOTE_MARKS = "¹²³⁴⁵⁶⁷⁸⁹⁰*†‡§"
_FOOTNOTE = re.compile(rf"[\d{_FOOTNOTE_MARKS}]+")
# "Sweden (SEK)", "Bahamas 2 3 (USD)", "Estonia, Latvia, and Lithuania (EUR):", "Peru (PEN)¹".
_HEADER = re.compile(
    rf"^(?P<names>[^()]*?[^\W\d_][^()]*?)\s*\(\s*(?P<cur>[A-Z]{{3}})\s*\)\s*:?"
    rf"\s*[\d\s{_FOOTNOTE_MARKS}]*$"
)
_NAME_SPLIT = re.compile(r"\s*(?:,|;|/|&|\band\b)\s*")
_BARE_NUMBER = re.compile(r"^\W*(\d[\d.,\s]*\d|\d)\W*$")
_BLOCKED = re.compile(r"access denied|you don't have permission to access|request unsuccessful")

_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "template", "iframe", "head", "-comment"}
)
_BLOCK_TAG_NAMES = (
    "address article aside blockquote body caption dd details dialog div dl dt fieldset "
    "figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr li main nav ol p "
    "pre section summary table tbody td tfoot th thead tr ul button option "
)
_BLOCK_TAGS = frozenset(_BLOCK_TAG_NAMES.split())
_WS = re.compile(r"[\s\u200b]+")


def _is_footnote_mark(sup: LexborNode) -> bool:
    """Short <sup> content (footnote "2", "◊") is dropped, not spaced out: "$59.99<sup>4</sup>"
    as "$59.99 4" would parse as 59994 because spaces are thousands separators."""
    return len((sup.text(deep=True) or "").strip()) <= 4


def _block_lines(root: LexborNode | None) -> list[str]:
    """Visible text, one line per block element, with inline markup joined.

    haggler.text.html_to_lines emits one line per text node, which splits "<b>Sweden</b> (SEK)"
    and "Bahamas<sup>2</sup> (USD)" into fragments; this keeps each block on one line and turns
    <br> into a line break. Footnote marks in <sup> are dropped.
    """
    lines: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        text = _WS.sub(" ", "".join(buf)).strip()
        buf.clear()
        if text:
            lines.append(text)

    def walk(node: LexborNode) -> None:
        for child in node.iter(include_text=True):
            if child.is_text_node:
                buf.append(child.text_content or "")
                continue
            tag = child.tag or ""
            if tag in _SKIP_TAGS or child.is_comment_node:
                continue
            if tag == "br":
                flush()
                continue
            if tag == "sup":
                if _is_footnote_mark(child):
                    continue
                buf.append(" ")
                walk(child)
                buf.append(" ")
                continue
            is_block = tag in _BLOCK_TAGS
            if is_block:
                flush()
            walk(child)
            if is_block:
                flush()

    if root is not None:
        walk(root)
        flush()
    return lines


@dataclass
class _Entry:
    """One tier price for the countries named by one header."""

    countries: list[CountryDef]
    currency: str
    plan_id: str
    label: str
    amount: Decimal
    evidence: str


@dataclass
class _Header:
    text: str
    countries: list[CountryDef]
    currency: str


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
    text = text.strip()
    if len(text) > 600:
        return None
    m = _HEADER.match(text)
    if not m:
        return None
    return _Header(
        text=f"{_clean_names(m.group('names'))} ({m['cur']})",
        countries=_countries_named(m.group("names"), cat),
        currency=m["cur"],
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
            )
        )
    return out


def _from_lines(lines: list[str], svc: ServiceDef, cat: Catalog) -> list[_Entry]:
    """Walk the article: a header opens a country block, tier lines fill it.

    The block closes at the next header, at the first non-tier line after its tiers, or after
    two unrelated lines, so footnotes and region headings are never attributed to a country.
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
            if header:
                found = _tier_entries(line, header, svc)
                if found:
                    entries.extend(found)
                    seen_tiers, gap = True, 0
                    continue
        parsed = _parse_header(line, cat)
        if parsed:
            header, seen_tiers, gap = parsed, False, 0
            continue
        if header:
            gap += 1
            if seen_tiers or gap > 2:
                header = None
    return entries


def _cell_text(cell: LexborNode) -> str:
    return " ".join(_block_lines(cell))


def _from_tables(tree: LexborHTMLParser, svc: ServiceDef, cat: Catalog) -> list[_Entry]:
    """Countries as rows and tiers as columns ("Country or region | 50GB | 200GB | ...")."""
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
            header = _parse_header(cells[0], cat)
            if header is None:
                cur = cells[currency_col].strip() if currency_col is not None else ""
                countries = _countries_named(cells[0], cat)
                if not countries:
                    continue
                if not re.fullmatch(r"[A-Z]{3}", cur):
                    cur = countries[0].currency
                header = _Header(
                    text=f"{_clean_names(cells[0])} ({cur})", countries=countries, currency=cur
                )
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
        entries = _from_tables(tree, svc, cat) + _from_lines(_block_lines(tree.body), svc, cat)

        wanted = set(task.countries)
        values: dict[tuple[str, str], Decimal] = {}
        drafts: list[ObservationDraft] = []
        for e in entries:
            for country in e.countries:
                if country.cc not in wanted:
                    continue
                key = (country.cc, e.plan_id)
                if key in values:
                    if values[key] != e.amount:
                        notes.append(
                            f"{country.cc} {e.plan_id}: kept {values[key]}, ignored {e.amount}"
                        )
                    continue
                values[key] = e.amount
                drafts.append(
                    draft(
                        task,
                        plan_id=e.plan_id,
                        label=e.label,
                        amount=e.amount,
                        currency=e.currency,
                        evidence=e.evidence,
                        country=country.cc,
                        flags=list(FLAGS),
                    )
                )

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
