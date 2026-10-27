"""YouTube Premium, Google One and HBO Max prices from App Store in-app purchase listings.

apps.apple.com/{cc}/app/id{app_id} picks the storefront from the URL (not the visitor's IP) and
lists up to ~10 "In-App Purchases" as (developer-localized name, storefront price) pairs:
"YouTube Premium  $18.99", "Premium  129,00 kr", "100 GB  US$ 1,99" (AR bills in USD). These are
Apple-billed prices, often above the vendor's web price, so every draft is flagged
iap_maybe_marked_up.

Apple rebuilt apps.apple.com as a Svelte app in 2025 and the markup is not something we can pin
down from here, so three extractors run, in order of trust:
  1. embedded JSON (application/json scripts such as serialized-server-data, and the older
     fastboot shoebox caches whose values are JSON strings): objects with a name/title and a
     (formatted) price under an IAP-ish key ("top-in-apps", "inAppPurchases") or under a node
     titled "In-App Purchases"; text pairs (["YouTube Premium", "$18.99"]);
  2. HTML lists: the pre-2025 li.list-with-numbers__item rows, and any list rows under a
     localized "In-App Purchases" heading (dt -> dd, or the heading's section);
  3. visible text: name/price line pairs right after that heading.
JSON objects and HTML rows hold a name and its price together, so their union is kept (the
JSON cache may list only the "top" purchases). The text extractor pairs consecutive lines, so it
only adds names the others did not report: a misaligned pairing cannot invent a second price for
a known item.

The listing carries no billing period. Several names mapping to one plan usually mean monthly
vs annual: explicit words in the name win ("Annual", "12 meses", "årsabonnemang"); otherwise a
price 8-12.5x another one of the same plan is annual, and any other duplicate keeps the lowest
price as monthly with the iap_duplicate flag. That holds for one name listed twice ("YouTube
Premium" at $18.99 and $15.99). Different names at different prices that land on one plan's
period only by default are more likely a variant the patterns do not know ("YouTube Premium
Estudante" read as Individual), so those items go to the LLM instead of publishing the lowest.

Two source params cut those LLM jobs where the listing is known to be noisy:
  - drop_unmatched: items no plan pattern matches are ignored instead of sent to the LLM (the
    YouTube app also sells Super Chat, movie rentals and channel memberships with any name);
  - lowest_across_names: different names on one plan's period are duplicates, so the lowest
    price is kept with the iap_duplicate flag (Deezer lists "Deezer Family" next to a cheaper
    regional SKU "Deezer Family Portugal").
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, CountryDef, ServiceDef, SourceDef
from haggler.models.observations import BillingPeriod, ObservationDraft
from haggler.models.run import Task
from haggler.money import find_prices, parse_money
from haggler.sources.base import (
    IapItem,
    LlmRequest,
    ParseResult,
    PerCountryAdapter,
    draft,
    match_iap_plan,
)
from haggler.text import text_node_lines

DEFAULT_URL = "https://apps.apple.com/{cc_lower}/app/id{app_id}"
FLAG_MARKUP = "iap_maybe_marked_up"
FLAG_DUPLICATE = "iap_duplicate"
# An annual price is typically 8-12.5 monthly prices (2-4 months free ... small premium).
YEAR_RATIO = (Decimal(8), Decimal("12.5"))
_APP_HOSTS = {"apps.apple.com", "itunes.apple.com"}

# ------------------------------------------------------------------ localized strings

_FOLD_TABLE = str.maketrans(
    {"ø": "o", "æ": "ae", "œ": "oe", "ł": "l", "đ": "d", "ð": "d", "ı": "i", "þ": "th"}
)


def fold(text: str) -> str:
    """Accent-free, case-folded, punctuation-collapsed text for matching localized UI strings.

    "Uygulama İçi Satın Almalar" -> "uygulama ici satin almalar", "In-App-Käufe" -> "in app kaufe".
    """
    s = text.casefold().translate(_FOLD_TABLE)
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


# Folded headings of the In-App Purchases section in the storefront languages we cover.
_IAP_HEADING = re.compile(
    r"(?:in app (?:purchases?|kaufe|aankopen|kob|kjop|kop|ostot)"
    r"|(?:compras?|achats?|acquisti|kop|kob|kjop|zakupy|nakupy|pembelian|mua(?: hang)?"
    r"|pirkimai|pirkiniai|pirkumi|achizitii)\b.{0,25}\b(?:apps?|appen|appar|aplikac\w*|aplikas\w*"
    r"|aplicat\w*|aplicac\w*|integr\w*|ung dung|programoje|lietotne)"
    r"|uygulama ici satin al\w*|sovelluksen sisaiset ostok?\w*|rakendusesisesed ostud"
    r"|(?:alkalmazason|appon|app on) beluli vasarlasok|عمليات الشراء داخل التطبيق)(?: \d+)?"
)
# Folded "this app is not available in your country or region" variants.
_NOT_AVAILABLE = re.compile(
    r"not (?:currently )?available in (?:your|the|this) (?:country|region|\w+ (?:app )?store)"
    r"|(?:isn t|is not) (?:currently )?available in your (?:country|region)"
    r"|no (?:esta|se encuentra) disponible (?:\w+ )?en (?:tu|su) (?:pais|region)"
    r"|nao esta disponivel (?:\w+ )?(?:no seu|em seu|no teu|na sua) (?:pais|regiao)"
    r"|inte tillganglig\w* i (?:ditt|din)"
    r"|in deinem land (?:oder|bzw)|nicht in deinem land|in ihrem land nicht"
    r"|n est pas disponible (?:\w+ )?dans votre (?:pays|region)"
    r"|non e disponibile (?:\w+ )?nel tuo paese"
    r"|ulkenizde (?:veya bolgenizde )?(?:mevcut degil|kullanilamiyor|bulunmuyor)"
)
_BLOCKED = re.compile(r"access denied|captcha|unusual traffic|too many requests|request blocked")

# Billing period words in IAP names / SKU titles (folded text).
_ONE = r"(?:1|one|un|una|uno|um|uma|ett|en|ein|eine|einen|bir|egy|jeden|jedna)"
_Y_UNIT = (
    r"(?:years?|yrs?|anos?|ar|jahre?|ans?|anno|yil|rok|jaar|vuosi|ev|aasta|metai|gads|nam|tahun)"
)
_M_UNIT = (
    r"(?:months?|mo|mths?|mes|meses|manad|manader|monat|monate|monaten|mois|mese|mesi|ay"
    r"|miesiac|miesiace|miesiecy|maand|maanden|kuukausi|kuukautta|honap|mesic|mesice|luna|luni"
    r"|thang|bulan|maned|maneder|maaned|maaneder)"
)
_NAME_YEAR = re.compile(
    rf"\b(?:annual\w*|yearly|anual\w*|arlig\w*|ars\w*(?:abonnemang|prenumeration|plan)"
    rf"|jahrlich\w*|jahres\w*|annuel\w*|annuale|yillik|roczn\w*|jaarlijks|aarlig\w*|vuosi\w*"
    rf"|eves|year|jahr|jaar|anno|{_ONE} {_Y_UNIT}|12 {_M_UNIT}|per {_Y_UNIT})\b"
)
_NAME_MONTH = re.compile(
    rf"\b(?:monthly|mensual\w*|mensal\w*|manatlig\w*|manads\w*|monatlich\w*|monats\w*"
    rf"|mensuel\w*|mensile|aylik|miesieczn\w*|maandelijks|kuukausi\w*|havi|mesicni|bulanan"
    rf"|maanedlig\w*|manedlig\w*|month|manad|monat|mes|mois|mese|maand|maned|maaned"
    rf"|{_ONE} {_M_UNIT}|per {_M_UNIT})\b"
)
_NAME_QUARTER = re.compile(rf"\b(?:quarter\w*|trimestr\w*|kvartal\w*|vierteljahr\w*|3 {_M_UNIT})\b")


def is_iap_heading(text: object) -> bool:
    if not isinstance(text, str) or len(text) > 80:
        return False
    folded = fold(text)
    return 0 < len(folded) <= 48 and bool(_IAP_HEADING.fullmatch(folded))


def name_period(text: str) -> BillingPeriod | None:
    """Billing period stated in an item name or SKU title; None when absent or contradictory."""
    folded = fold(text)
    hits: list[BillingPeriod] = [
        period
        for period, rx in (("year", _NAME_YEAR), ("quarter", _NAME_QUARTER), ("month", _NAME_MONTH))
        if rx.search(folded)
    ]
    return hits[0] if len(hits) == 1 else None


def _yearly_multiple(hi: Decimal, lo: Decimal) -> bool:
    return lo > 0 and YEAR_RATIO[0] <= hi / lo <= YEAR_RATIO[1]


def infer_periods(
    amounts: Sequence[Decimal],
    known: Sequence[tuple[Decimal, BillingPeriod]] = (),
    *,
    default: BillingPeriod = "month",
) -> list[tuple[BillingPeriod, str]]:
    """(period, reason) for each amount whose name/title stated no period.

    An amount 8-12.5x a known monthly (or another unknown) amount is annual; one that a known
    annual (or another unknown) amount is 8-12.5x of is monthly; anything else gets `default`.
    """
    out: list[tuple[BillingPeriod, str]] = []
    for i, amount in enumerate(amounts):
        others = [v for j, v in enumerate(amounts) if j != i]
        if any(p == "month" and _yearly_multiple(amount, a) for a, p in known):
            out.append(("year", "8-12.5x a monthly price"))
        elif any(p == "year" and _yearly_multiple(a, amount) for a, p in known):
            out.append(("month", "an annual price is 8-12.5x it"))
        elif any(_yearly_multiple(amount, v) for v in others):
            out.append(("year", "8-12.5x a sibling price"))
        elif any(_yearly_multiple(v, amount) for v in others):
            out.append(("month", "a sibling price is 8-12.5x it"))
        else:
            out.append((default, "default"))
    return out


# ------------------------------------------------------------------ small helpers

_ORDINAL = re.compile(r"\d{1,2}[.)]?")
_NAME_STRIP = " \t-–—:·|•*"


def _clean(text: str) -> str:
    return " ".join(text.replace("\u200b", "").split())


def _name_key(name: str) -> str:
    return fold(name)


def split_price(text: str, expected: list[str]) -> tuple[str, str] | None:
    """(name, price) when `text` ends with a price ("YouTube Premium $18.99" or just "$18.99")."""
    prices = find_prices(text, expected)
    if not prices:
        return None
    last = prices[-1]
    if len(text[last.end :].strip()) > 3:
        return None  # the price is not at the end: prose, not a listing row
    name = text[: last.start].strip(_NAME_STRIP)
    if name and find_prices(name, expected):
        return None
    return name, text[last.start : last.end].strip()


def _with_amount(item: IapItem, expected: list[str]) -> IapItem:
    if item.amount is not None:
        return item
    money = parse_money(item.price_text, expected)
    if money is None:
        return item
    return replace(item, amount=money.amount, currency=money.currency)


def _dedupe(items: list[IapItem]) -> list[IapItem]:
    seen: set[tuple[str, str]] = set()
    out: list[IapItem] = []
    for it in items:
        key = (_name_key(it.name), _clean(it.price_text))
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


# ------------------------------------------------------------------ 1. embedded JSON

_NAME_KEYS = (
    "name",
    "title",
    "leadingText",
    "primaryText",
    "text",
    "label",
    "displayName",
    "productName",
)
_PRICE_KEYS = (
    "priceFormatted",
    "formattedPrice",
    "displayPrice",
    "priceString",
    "localizedPrice",
    "priceDisplay",
    "formatted_price",
    "trailingText",
    "price",
    # Generic secondary-text slots, only read inside an IAP node and only if they hold a price.
    "subtitle",
    "secondaryText",
    "accessoryText",
    "detailText",
    "value",
)
_CURRENCY_KEYS = ("currencyCode", "currency", "priceCurrency")
_KIND_KEYS = ("type", "$kind", "kind", "contentType", "id")
_TITLE_KEYS = ("title", "name", "heading", "header", "text")
# "top-in-apps", "in_app", "inAppPurchases" (case-insensitive, not "mainApp")...
_HINT_A = re.compile(r"(?:^|[^a-z])in[-_ ]?app", re.IGNORECASE)
# ... "topInApps", "IAP", "iapItems" (case-sensitive).
_HINT_B = re.compile(r"InApp|IAP|(?:^|[^A-Za-z])iaps?(?![a-z])")
_IDENT = re.compile(r"[\w$-]{1,48}")


def _hint_word(value: str) -> bool:
    return bool(_IDENT.fullmatch(value) and (_HINT_A.search(value) or _HINT_B.search(value)))


def _dict_hinted(d: dict[str, Any]) -> bool:
    if any(isinstance(v := d.get(k), str) and _hint_word(v) for k in _KIND_KEYS):
        return True
    return any(is_iap_heading(d.get(k)) for k in _TITLE_KEYS)


def _text(value: Any) -> str | None:
    if isinstance(value, str) and (s := _clean(value)):
        return s
    return None


def _priced(name: str, src: dict[str, Any], expected: list[str]) -> IapItem | None:
    texts = [s for k in _PRICE_KEYS if (s := _text(src.get(k)))]
    if priced := next((t for t in texts if find_prices(t, expected)), None):
        return IapItem(name=name, price_text=priced)
    text = texts[0] if texts else None
    num = src.get("price")
    cur = next((s for k in _CURRENCY_KEYS if (s := _text(src.get(k)))), None)
    is_number = isinstance(num, int | float) and not isinstance(num, bool)
    if is_number and cur and re.fullmatch(r"[A-Za-z]{3}", cur):
        amount = Decimal(str(num))
        label = text or f"{amount} {cur.upper()}"
        return IapItem(name=name, price_text=label, amount=amount, currency=cur.upper())
    return None


def _json_item(d: dict[str, Any], expected: list[str]) -> IapItem | None:
    name = next((s for k in _NAME_KEYS if (s := _text(d.get(k)))), None)
    if not name or len(name) > 120 or is_iap_heading(name):
        return None
    offers = d.get("offers")
    offers = offers if isinstance(offers, list) else [offers]
    for src in (d, *(o for o in offers if isinstance(o, dict))):
        if item := _priced(name, src, expected):
            return item
    return None


def _walk(
    obj: Any,
    hinted: bool,
    out: list[tuple[IapItem, bool, bool]],
    expected: list[str],
    depth: int = 0,
) -> None:
    """Collect (item, hinted, pair_shaped); `hinted` = inside an IAP-ish key or titled node."""
    if depth > 64:
        return
    if isinstance(obj, list):
        for x in obj:
            _walk(x, hinted, out, expected, depth + 1)
        return
    if not isinstance(obj, dict):
        return
    here = hinted or _dict_hinted(obj)
    if item := _json_item(obj, expected):
        out.append((item, here, "leadingText" in obj))
    for key, value in obj.items():
        k = str(key)
        if "pair" in k.casefold() and isinstance(value, list):
            for pair in value:
                if not (isinstance(pair, list) and len(pair) == 2):
                    continue
                left, right = (_text(x) for x in pair)
                if left and right and not is_iap_heading(left) and find_prices(right, expected):
                    out.append((IapItem(name=left, price_text=right), here, True))
        _walk(value, here or _hint_word(k), out, expected, depth + 1)


def _expand(obj: Any, depth: int = 0) -> Any:
    """Decode JSON-encoded strings in place (shoebox caches store each API response as text)."""
    if depth > 4:
        return obj
    if isinstance(obj, str):
        s = obj.strip()
        if len(s) > 2 and s[0] in "{[" and s[-1] in "}]":
            try:
                return _expand(json.loads(s), depth + 1)
            except ValueError:
                return obj
        return obj
    if isinstance(obj, list):
        return [_expand(x, depth) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand(v, depth) for k, v in obj.items()}
    return obj


def json_blobs(tree: LexborHTMLParser) -> list[Any]:
    """Parsed ld+json / application/json / __NEXT_DATA__ scripts plus fastboot shoebox caches.

    haggler.text.json_scripts plus the shoebox script types, on an already parsed tree.
    """
    blobs: list[Any] = []
    for node in tree.css("script"):
        typ = (node.attributes.get("type") or "").lower()
        ident = node.attributes.get("id") or ""
        if "json" not in typ and "shoebox" not in typ and ident != "__NEXT_DATA__":
            continue
        try:
            blobs.append(json.loads(node.text() or ""))
        except ValueError:
            continue
    return blobs


def text_lines(html: str) -> list[str]:
    """Visible text, one line per text node (haggler.text.text_node_lines): every App Store
    row field is its own text node, so names never pick up a neighbour's text."""
    return text_node_lines(html)


def items_from_json(tree: LexborHTMLParser, expected: list[str]) -> list[IapItem]:
    found: list[tuple[IapItem, bool, bool]] = []
    for blob in json_blobs(tree):
        _walk(_expand(blob), False, found, expected)
    hinted = [it for it, h, _ in found if h]
    if hinted:
        return _dedupe(hinted)
    # Unlabelled text pairs only carry prices in the IAP annotation; named objects with a price
    # outside an IAP node may be paid apps in "More by this developer" shelves.
    return _dedupe([it for it, _, pair in found if pair])


# ------------------------------------------------------------------ 2. HTML lists

_ROW_CSS = "li, tr, [class*='text-pair'], [class*='textpair'], [class*='TextPair']"


def _leaf_texts(node: LexborNode) -> list[str]:
    out: list[str] = []
    for n in node.traverse(include_text=True):
        if not n.is_text_node or (n.parent is not None and n.parent.tag in {"script", "style"}):
            continue
        if t := _clean(n.text_content or ""):
            out.append(t)
    return out


def _row_item(row: LexborNode, expected: list[str]) -> IapItem | None:
    leaves = [t for t in _leaf_texts(row) if not _ORDINAL.fullmatch(t)]
    if not leaves or len(leaves) > 6 or sum(map(len, leaves)) > 200:
        return None
    for k in range(len(leaves) - 1, -1, -1):
        split = split_price(leaves[k], expected)
        if split is None:
            continue
        name = _clean(" ".join([*leaves[:k], split[0]])).strip(_NAME_STRIP)
        if name and not find_prices(name, expected) and not is_iap_heading(name):
            return IapItem(name=name, price_text=split[1])
        return None
    return None


def _following_element(node: LexborNode) -> LexborNode | None:
    sib = node.next
    while sib is not None and not sib.is_element_node:
        sib = sib.next
    return sib


def _iap_sections(tree: LexborHTMLParser, expected: list[str]) -> Iterator[LexborNode]:
    """Containers of the rows that follow each localized "In-App Purchases" heading."""
    root = tree.body
    if root is None:
        return
    for text_node in root.traverse(include_text=True):
        if not text_node.is_text_node or not is_iap_heading(text_node.text_content or ""):
            continue
        el = text_node.parent
        if el is None or el.tag in {"script", "style", "title", "option"}:
            continue
        # <dt>In-App Purchases</dt><dd>...</dd> (also with the text wrapped in a span).
        dt, probe = None, el
        for _ in range(3):
            if probe is None:
                break
            if probe.tag == "dt":
                dt = probe
                break
            probe = probe.parent
        if dt is not None and (dd := _following_element(dt)) is not None and dd.tag == "dd":
            yield dd
            continue
        # Otherwise the closest ancestor (a few levels up) that holds priced rows.
        anc = el
        for _ in range(4):
            anc = anc.parent
            if anc is None or anc.tag in {"body", "html"}:
                break
            if any(_row_item(r, expected) for r in anc.css(_ROW_CSS)):
                yield anc
                break


def items_from_html(tree: LexborHTMLParser, expected: list[str]) -> list[IapItem]:
    out: list[IapItem] = []
    # Pre-2025 design: <li class="list-with-numbers__item"> title + price spans.
    for li in tree.css("li.list-with-numbers__item"):
        title = li.css_first(".list-with-numbers__item__title") or li.css_first(
            ".truncate-single-line"
        )
        price = li.css_first(".list-with-numbers__item__price") or li.css_first("[class*=price]")
        name = _clean(title.text(deep=True)) if title else ""
        price_text = _clean(price.text(deep=True)) if price else ""
        if name and price_text and find_prices(price_text, expected):
            out.append(IapItem(name=name, price_text=price_text))
    for section in _iap_sections(tree, expected):
        for row in section.css(_ROW_CSS):
            if item := _row_item(row, expected):
                out.append(item)
    return _dedupe(out)


# ------------------------------------------------------------------ 3. visible text


def _pairs_after(lines: list[str], start: int, expected: list[str]) -> list[IapItem]:
    """Name/price pairs from `start`; stops at the first line that breaks the pattern."""
    items: list[IapItem] = []
    i, skipped = start, 0
    while i < len(lines) and len(items) < 30:
        line = lines[i]
        if _ORDINAL.fullmatch(line):
            i += 1
            continue
        if is_iap_heading(line):
            break
        split = split_price(line, expected)
        if split and split[0]:
            items.append(IapItem(name=split[0], price_text=split[1]))
            i += 1
            continue
        if split is None and len(line) <= 100 and i + 1 < len(lines):
            nxt = split_price(lines[i + 1], expected)
            if nxt and not nxt[0]:
                items.append(IapItem(name=line, price_text=nxt[1]))
                i += 2
                continue
        if items:
            break
        skipped += 1  # e.g. a "Yes" summary between the heading and the list
        if skipped > 3:
            break
        i += 1
    return items


def items_from_text(lines: list[str], expected: list[str]) -> list[IapItem]:
    best: list[IapItem] = []
    for i, line in enumerate(lines):
        if is_iap_heading(line):
            items = _pairs_after(lines, i + 1, expected)
            if len(items) > len(best):
                best = items
    return _dedupe(best)


# ------------------------------------------------------------------ merge


def collect_items(
    html: str, tree: LexborHTMLParser, expected: list[str]
) -> tuple[list[IapItem], list[str]]:
    """All IAP rows from the three extractors, merged by trust (see module docstring)."""
    # (label, items, row-based): row-based extractors read name and price from one element.
    strategies = [
        ("json", items_from_json(tree, expected), True),
        ("html", items_from_html(tree, expected), True),
        ("text", items_from_text(text_lines(html), expected), False),
    ]
    merged: list[IapItem] = []
    notes: list[str] = []
    seen: set[tuple[str, Decimal | None, str | None]] = set()
    for label, raw, row_based in strategies:
        if not raw:
            continue
        known_names = {_name_key(it.name) for it in merged}
        added = 0
        for it in (_with_amount(x, expected) for x in raw):
            nk = _name_key(it.name)
            key = (nk, it.amount, it.currency)
            if key in seen:
                continue
            if not row_based and nk in known_names:
                notes.append(f"{label}: {it.name} {it.price_text} conflicts with rows; ignored")
                continue
            seen.add(key)
            merged.append(it)
            added += 1
        notes.append(f"{label}: {len(raw)} items ({added} new)")
    return merged, notes


# ------------------------------------------------------------------ response checks


def _storefront(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.netloc.lower() not in _APP_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if segments and re.fullmatch(r"[A-Za-z]{2}", segments[0]):
        return segments[0].lower()
    return None


def _response_problem(resp: RawResponse, task: Task, country: CountryDef) -> ParseResult | None:
    body = resp.text or ""
    if resp.status_code in (403, 429) or (len(body) < 6000 and _BLOCKED.search(body.casefold())):
        return ParseResult(
            status="blocked", notes=[f"bot wall / rate limit (HTTP {resp.status_code})"]
        )
    if resp.status_code in (404, 410):
        return ParseResult(
            status="not_available",
            notes=[f"HTTP {resp.status_code}: no app page in this storefront"],
        )
    final = resp.final_url or resp.url or task.url
    parts = urlsplit(final)
    if parts.netloc and parts.netloc.lower() not in _APP_HOSTS:
        return ParseResult(status="not_available", notes=[f"redirected off the App Store: {final}"])
    cc = country.cc.lower()
    if (sf := _storefront(final)) and sf != cc:
        return ParseResult(
            status="wrong_country", notes=[f"storefront /{sf}/ instead of /{cc}/: {final}"]
        )
    app_id = str(task.params.get("app_id") or "")
    if app_id and parts.path and f"id{app_id}" not in parts.path:
        return ParseResult(status="not_available", notes=[f"redirected away from the app: {final}"])
    return None


def _canonical_problem(tree: LexborHTMLParser, country: CountryDef) -> str | None:
    """Replayed pages keep the requested URL, so canonical/og:url are a second storefront check."""
    for selector, attr in (
        ('link[rel="canonical"]', "href"),
        ('meta[property="og:url"]', "content"),
    ):
        node = tree.css_first(selector)
        href = (node.attributes.get(attr) or "") if node else ""
        sf = _storefront(href) if href.startswith("http") else None
        if sf and sf != country.cc.lower():
            return f"{selector} is the /{sf}/ storefront: {href}"
    return None


# ------------------------------------------------------------------ adapter


class AppStoreIapAdapter(PerCountryAdapter):
    id: ClassVar[str] = "appstore_iap"

    def plan(self, svc: ServiceDef, src: SourceDef, cat: Catalog) -> list[Task]:
        params = {"url": DEFAULT_URL, **src.params}
        tasks = super().plan(svc, src.model_copy(update={"params": params}), cat)
        if src.params.get("app_id"):
            return tasks
        return [
            t.model_copy(
                update={"url": "", "host": "", "params": {**t.params, "not_configured": ["app_id"]}}
            )
            for t in tasks
        ]

    def extra_vars(self, src: SourceDef) -> dict[str, object]:
        return {"app_id": src.params.get("app_id", "")}

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        svc = cat.service(task.service_ids[0])
        country = cat.country(task.countries[0])
        expected = list(country.expected_currencies(task.channel))
        if problem := _response_problem(resp, task, country):
            return problem
        tree = LexborHTMLParser(resp.text)
        if issue := _canonical_problem(tree, country):
            return ParseResult(status="wrong_country", notes=[issue])

        items, notes = collect_items(resp.text, tree, expected)
        if not items:
            if _NOT_AVAILABLE.search(fold(" ".join(text_lines(resp.text)))):
                return ParseResult(
                    status="not_available", notes=["app not offered in this storefront"]
                )
            return ParseResult(
                status="no_prices", notes=[*notes, "no In-App Purchases listing found"]
            )
        return self._map(task, svc, country, items, expected, notes)

    def _map(
        self,
        task: Task,
        svc: ServiceDef,
        country: CountryDef,
        items: list[IapItem],
        expected: list[str],
        notes: list[str],
    ) -> ParseResult:
        excludes = [re.compile(p, re.IGNORECASE) for p in task.params.get("exclude_patterns") or []]
        by_plan: dict[str, list[IapItem]] = {}
        unmapped: list[IapItem] = []
        foreign: Counter[str] = Counter()
        excluded = 0
        dropped = 0
        drop_unmatched = bool(task.params.get("drop_unmatched"))
        for it in items:
            if it.amount is None or it.amount <= 0:
                notes.append(f"unpriced: {it.name} {it.price_text}")
                continue
            if any(rx.search(it.name) for rx in excludes):
                excluded += 1
                continue
            if it.currency not in expected:
                foreign[it.currency or "?"] += 1
                continue
            plans = match_iap_plan(svc, it.name)
            if len(plans) == 1:
                by_plan.setdefault(plans[0], []).append(it)
                continue
            if plans:
                notes.append(f"ambiguous: {it.name} -> {', '.join(plans)}")
            elif drop_unmatched:
                dropped += 1
                continue
            unmapped.append(it)
        if excluded:
            notes.append(f"excluded {excluded} item(s) by exclude_patterns")
        if dropped:
            notes.append(f"dropped {dropped} item(s) no plan pattern matches (drop_unmatched)")
        if foreign:
            seen = ", ".join(f"{c}x{n}" for c, n in sorted(foreign.items()))
            notes.append(f"ignored prices in {seen}; expected {', '.join(expected)}")

        observations: list[ObservationDraft] = []
        for plan in svc.plans:
            if plan.id in by_plan:
                drafts, deferred = self._plan_drafts(task, plan.id, by_plan[plan.id], notes)
                observations += drafts
                unmapped += deferred
        published = {o.plan_id for o in observations}

        llm_requests: list[LlmRequest] = []
        if unmapped:
            llm_requests.append(
                LlmRequest(
                    service_id=svc.id,
                    country=country.cc,
                    reason="unmapped_iap",
                    page_text="\n".join(f"{it.name} — {it.price_text}" for it in unmapped),
                )
            )
        if svc.default_plan in published:
            status = "ok"
        elif observations or unmapped:
            status = "partial"
        elif foreign:
            status = "wrong_country"
        else:
            status = "no_prices"
        return ParseResult(
            observations=observations,
            status=status,
            missing_plans=[p.id for p in svc.plans if p.id not in published],
            unmapped=unmapped,
            llm_requests=llm_requests,
            notes=notes,
        )

    def _plan_drafts(
        self, task: Task, plan_id: str, items: list[IapItem], notes: list[str]
    ) -> tuple[list[ObservationDraft], list[IapItem]]:
        """Drafts per billing period, and the items left for the LLM to map (module docstring)."""
        stated = [(it, name_period(it.name)) for it in items]
        known = [(it.amount, p) for it, p in stated if p and it.amount is not None]
        unknown = [it for it, p in stated if p is None]
        groups: dict[BillingPeriod, list[IapItem]] = {}
        for it, p in stated:
            if p:
                groups.setdefault(p, []).append(it)
        defaulted: set[int] = set()  # id() of items whose period nothing stated or implied
        inferred = infer_periods([it.amount or Decimal(0) for it in unknown], known)
        for it, (period, reason) in zip(unknown, inferred, strict=True):
            groups.setdefault(period, []).append(it)
            if reason == "default":
                defaulted.add(id(it))
            else:
                notes.append(f"{plan_id}: {it.price_text} read as {period} ({reason})")

        deferred: list[IapItem] = []
        out: list[ObservationDraft] = []
        for period in ("month", "quarter", "year"):
            group = groups.get(period)
            if not group:
                continue
            guessed = [it for it in group if id(it) in defaulted]
            if (
                guessed
                and not task.params.get("lowest_across_names")
                and len({it.amount for it in group}) > 1
                and len({_name_key(it.name) for it in group}) > 1
            ):
                deferred += guessed
                names = "; ".join(f"{it.name} {it.price_text}" for it in group)
                notes.append(
                    f"{plan_id}: different names at different {period} prices ({names}); "
                    f"{len(guessed)} item(s) without a stated period sent to the LLM"
                )
                group = [it for it in group if id(it) not in defaulted]
                if not group:
                    continue
            chosen = min(group, key=lambda it: it.amount or Decimal(0))
            flags = [FLAG_MARKUP]
            distinct = sorted({it.amount for it in group if it.amount is not None})
            if len(distinct) > 1:
                flags.append(FLAG_DUPLICATE)
                notes.append(
                    f"{plan_id}: {len(distinct)} {period} prices "
                    f"({', '.join(str(a) for a in distinct)}); kept the lowest"
                )
            out.append(
                draft(
                    task,
                    plan_id=plan_id,
                    label=chosen.name,
                    amount=chosen.amount or Decimal(0),
                    currency=chosen.currency or "",
                    period=period,
                    evidence=f"{chosen.name} {chosen.price_text}",
                    flags=flags,
                )
            )
        return out, deferred
