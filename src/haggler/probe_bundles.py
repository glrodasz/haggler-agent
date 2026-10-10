"""`haggler probe --mode bundles`: can our fetcher read the official pages of the bundles?

WHY: Phase 3 watches the promotional bundles (data/curated/bundles.yaml) on their official pages.
Before building the watcher, this probe answers from a GitHub runner, per candidate URL: robots.txt,
HTTP status, final host (still the requested site?), content type, walls, the visible lines that
carry an amount in the country's currency, the lines naming the bundle or its services, and the
price-like values inside embedded JSON. The readability matrix it prints decides each bundle's
watch mode (only a `readable` page may be `auto`), and the watcher's anchors are copied from it.
A PDF is read as its extracted text (haggler.fetch.pdf: pypdf in a sandboxed worker, layout-mode
lines) and gets the same amount and component lines, plus its extraction status and page count.
Unreadable pages (bot or login walls, robots.txt, JavaScript-only prices, encrypted or scanned
PDFs) are reported with their reason and left to a manual check: they are never worked around.

Targets file (probe/bundle_targets.txt): one target per non-comment line, "<label> <CC> <url>":
  label  a bundle_id of data/curated/bundles.yaml, or a free snake_case label for a page that is
         not in it yet;
  CC     the catalog country whose site and currency the page must show (for a known bundle_id,
         the bundle's own country);
  url    https:// on a public host.
"#" starts a comment (at the start of a line or after a space: URL fragments are kept); a ";"
next to whitespace also separates targets, like the probe.yml override.

Output: <out>/summary.{json,md} quote at most QUOTE_BUDGET characters of page text per target
(title, amount lines, component lines, embedded snippets, JSON script ids; each line at most
LINE_CHARS) and are the only files meant to be published. Raw bodies (a PDF's bytes too) go
under <out>/raw/bundles/ for local debugging only. A PDF's text is quoted only through the same
amount and component lines, within the same budget. `pdf_pending` is left for a PDF whose
bytes the fetcher did not deliver (nothing to extract).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from haggler.catalog import load_bundles
from haggler.config import Settings
from haggler.fetch import Fetcher, RawResponse
from haggler.fetch.client import looks_like_bot_wall
from haggler.fetch.pdf import PdfText, extract_pdf_text
from haggler.models.catalog import Catalog, CountryDef
from haggler.models.curated import Bundle, BundlesFile
from haggler.money import find_prices
from haggler.probe import (
    _DIGIT,
    _JSON_PRICE_LINE,
    _STATE_ASSIGN,
    TITLE_CHARS,
    _cell,
    _exc_text,
    _fetch_failure,
    _fetch_url,
    _safe_name,
    json_script_ids,
    page_lines,
    page_title,
)
from haggler.research.evidence import host_of, public_host, site_of

# Verbatim page text a summary row may quote, in total (title + all quoted lines).
QUOTE_BUDGET = 4000
LINE_CHARS = 200
MAX_AMOUNT_LINES = 20
MAX_COMPONENT_LINES = 10
MAX_EMBEDDED_SNIPPETS = 5
MAX_SCRIPT_IDS = 20
# Visible text shorter than this is most likely painted by JavaScript.
THIN_TEXT_CHARS = 400
# Bounds for embedded data (a __NEXT_DATA__ blob can be megabytes and find_prices is slow on
# bulk text): blobs up to MAX_BLOB_CHARS are parsed as JSON, string leaves longer than
# MAX_LEAF_CHARS are skipped, a blob that is not JSON is scanned up to MAX_SCAN_CHARS, and the
# count stops at MAX_EMBEDDED_HITS (only MAX_EMBEDDED_SNIPPETS are quoted anyway).
MAX_JSON_NODES = 200_000
MAX_BLOB_CHARS = 5_000_000
MAX_LEAF_CHARS = 2_000
MAX_SCAN_CHARS = 200_000
MAX_EMBEDDED_HITS = 200
PATH_CHARS = 80

# The `readability` vocabulary of a summary row (what fixes a bundle's watch mode).
READABILITY = (
    "readable",
    "embedded_only",
    "no_amounts",
    "pdf_unreadable",
    "pdf_pending",
    "wrong_country",
    "blocked",
    "robots_disallowed",
    "failed",
)

_LABEL = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_COMMENT = re.compile(r"(?:^|\s)#.*$")
_SEPARATOR = re.compile(r"(?:^|(?<=\s));|;(?=\s|$)")
# Sign-in paths, and account-verification walls (Mercado Libre sends some pages to
# /gz/account-verification?go=...): either way the page is behind the provider's account.
_LOGIN_PATH = re.compile(
    r"(?:^|/)(?:login|log-in|signin|sign-in|sign_in|logon|auth|authorize|oauth2?|sso|servicelogin"
    r"|lgz|account-verification)(?:$|[/?._-])",
    re.IGNORECASE,
)
# Hosts that only serve sign-in (accounts.google.com, idmsa.apple.com, login.microsoftonline...).
_LOGIN_HOST = re.compile(r"^(?:login|signin|accounts?|auth|sso|idmsa|appleid)\.", re.IGNORECASE)
# Response headers that mark a challenge by themselves on a 2xx page ("server" only feeds the
# Akamai deny-page test, which also needs its text). A bare `x-datadome` is not one: DataDome
# sends it on ordinary responses too.
_CHALLENGE_HEADERS = frozenset({"cf-mitigated", "x-amzn-waf-action", "server"})
# A bare "$" is shared with USD and the other dollars. In the currencies whose consumer prices
# run in the thousands, a bare-"$" amount below this floor is most likely a foreign price (a page
# that localises by IP shows the runner's US price), so it is not counted as the local currency.
_DOLLAR_FLOOR = {"COP": Decimal(1000), "CLP": Decimal(500), "ARS": Decimal(500)}
_NUMERIC = re.compile(r"\d[\d.,\s]*")
# A price-like key and its number in a JavaScript literal; no unbounded prefix, so a long
# identifier or base64 run is scanned once (an unbounded \w* would retry it from every letter).
_PRICE_KV = re.compile(
    r"""(?:price|amount|cost|precio|preis|pris)\w{0,24}["']?\s{0,3}:\s{0,3}["']?\d[\d.,]*""",
    re.IGNORECASE,
)
_WS = re.compile(r"\s+")
_BACKTICKS = re.compile(r"`+")
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>&!|~#])")
# What may precede the "%PDF-" signature once the body is decoded (BOM, NULs, whitespace).
_PDF_LEAD = "\ufeff\x00 \t\r\n"


# ============================================================================ targets


@dataclass(frozen=True)
class BundleTarget:
    label: str
    country: str
    url: str
    line: int = 0


def parse_bundle_targets(text: str, catalog: Catalog, bundles: BundlesFile) -> list[BundleTarget]:
    """Parse a bundle targets file; every bad line is reported with its number."""
    ccs = set(catalog.country_codes())
    known = {b.bundle_id: b for b in bundles.bundles}
    targets: list[BundleTarget] = []
    seen: set[tuple[str, str, str]] = set()
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        for chunk in _SEPARATOR.split(_COMMENT.sub("", raw)):
            fields = chunk.split()
            if not fields:
                continue
            problem = _target_problem(fields, ccs, known)
            if problem:
                errors.append(f"línea {n}: {problem}")
                continue
            label, cc, url = fields[0], fields[1].upper(), fields[2]
            if (label, cc, url) not in seen:
                seen.add((label, cc, url))
                targets.append(BundleTarget(label, cc, url, n))
    if errors:
        raise ValueError("targets de paquetes inválidos:\n  " + "\n  ".join(errors))
    if not targets:
        raise ValueError("el archivo no tiene ningún target de paquetes ('<etiqueta> <CC> <url>')")
    return targets


def _target_problem(fields: list[str], ccs: set[str], known: dict[str, Bundle]) -> str | None:
    if len(fields) < 3:
        return f"faltan campos en {' '.join(fields)!r} (se espera '<etiqueta> <CC> <url>')"
    if len(fields) > 3:
        return f"sobran campos en {' '.join(fields)!r} (se espera '<etiqueta> <CC> <url>')"
    label, cc, url = fields[0], fields[1].upper(), fields[2]
    if not _LABEL.match(label):
        return f"etiqueta inválida {label!r} (un bundle_id o snake_case en minúsculas)"
    if cc not in ccs:
        return f"país desconocido {fields[1]!r} (debe estar en catalog/countries.yaml)"
    if label in known and known[label].country != cc:
        return f"el paquete {label} es de {known[label].country}, no de {cc}"
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return f"URL inválida {url!r}"
    if parts.scheme != "https" or not hostname:
        return f"la URL debe ser https://, no {url!r}"
    if parts.username or parts.password:
        return f"la URL no puede llevar usuario ni contraseña: {url!r}"
    if not public_host(hostname):
        return f"el host {hostname!r} no es público"
    return None


# ============================================================================ page analysis


def _clip(text: str, start: int = 0, end: int = 0, width: int = LINE_CHARS) -> str:
    """At most `width` characters of `text`, centred on [start, end) when it must be cut;
    a cut side is marked with "…" (so a quote is never mistaken for the whole line)."""
    if len(text) <= width:
        return text
    inner = width - 2
    lo = max(0, min(start - max(0, inner - (end - start)) // 2, len(text) - inner))
    hi = lo + inner
    return ("…" if lo > 0 else "") + text[lo:hi] + ("…" if hi < len(text) else "")


@dataclass
class AmountScan:
    """What amount_lines found in the visible text."""

    # Distinct lines with an amount in the expected currency, clipped around the first one.
    lines: list[str] = field(default_factory=list)
    # Amounts in other currencies ("?" = an ambiguous marker, or a bare "$" below _DOLLAR_FLOOR).
    others: Counter[str] = field(default_factory=Counter)
    # The unclipped visible lines behind `lines` (not quoted again as component lines).
    source: set[str] = field(default_factory=set)
    # Bare-"$" amounts too small to be the expected currency.
    below_floor: int = 0


def amount_lines(lines: list[str], currency: str) -> AmountScan:
    """The visible lines with an amount in `currency`, and the amounts in other currencies."""
    floor = _DOLLAR_FLOOR.get(currency)
    scan = AmountScan()
    hits: dict[str, None] = {}
    for line in lines:
        if not _DIGIT.search(line):
            continue
        # Amounts whose marker alone names no currency (a bare "$"), by position.
        shared = (
            {(p.start, p.end) for p in find_prices(line) if p.currency is None}
            if floor is not None
            else set()
        )
        mine = []
        for price in find_prices(line, [currency]):
            if price.currency != currency:
                scan.others[price.currency or "?"] += 1
            elif floor is not None and price.amount < floor and (price.start, price.end) in shared:
                scan.others["?"] += 1
                scan.below_floor += 1
            else:
                mine.append(price)
        if mine:
            hits.setdefault(_clip(line, mine[0].start, mine[0].end), None)
            scan.source.add(line)
    scan.lines = list(hits)
    return scan


def component_terms(bundle: Bundle, catalog: Catalog) -> list[str]:
    """Names a bundle page is expected to mention: the bundle's name and tier, and the catalog
    name of every component service (also without a trailing "Premium": "ViX Premium" -> "ViX")."""
    terms = [bundle.name, bundle.tier.replace("_", " ")]
    for comp in bundle.components:
        try:
            name = catalog.service(comp.service_id).name
        except KeyError:
            name = comp.service_id.replace("_", " ")
        terms.append(name)
        short = re.sub(r"\s+premium$", "", name, flags=re.IGNORECASE)
        if short != name:
            terms.append(short)
    return list(dict.fromkeys(t for t in terms if len(t) >= 3))


def component_lines(lines: list[str], terms: list[str], skip: set[str]) -> list[str]:
    """Distinct visible lines naming one of `terms`, clipped around it; the lines in `skip`
    (already quoted as amount lines) are left out."""
    if not terms:
        return []
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    pattern = re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.IGNORECASE)
    out: dict[str, None] = {}
    for line in lines:
        if line in skip:
            continue
        m = pattern.search(line)
        if m:
            out.setdefault(_clip(line, m.start(), m.end()), None)
    return list(out)


def _script_blobs(html: str) -> Iterator[tuple[str, str, bool]]:
    """(name, text, is_json) of every embedded data blob: JSON <script>s (ld+json, __NEXT_DATA__,
    application/json) and inline `window.__STATE__ = ...` assignments (text from the "=" on)."""
    for node in LexborHTMLParser(html).css("script"):
        attrs = node.attributes
        stype = (attrs.get("type") or "").lower()
        body = node.text() or ""
        if "json" in stype:
            name = attrs.get("id") or attrs.get("data-target") or stype
            yield _WS.sub(" ", name).strip(), body, True
        elif not attrs.get("src"):
            for m in _STATE_ASSIGN.finditer(body):
                yield m.group(1), body[m.end() :], False


def _leaves(value: Any) -> Iterator[tuple[str, str, Any]]:
    """(path, key, leaf) of a JSON value, depth first, at most MAX_JSON_NODES nodes."""
    stack: list[tuple[str, str, Any]] = [("", "", value)]
    visited = 0
    while stack and visited < MAX_JSON_NODES:
        path, key, node = stack.pop()
        visited += 1
        if isinstance(node, dict):
            items = [(f"{path}.{k}" if path else str(k), str(k), v) for k, v in node.items()]
            stack.extend(reversed(items))
        elif isinstance(node, list):
            stack.extend(reversed([(f"{path}[{i}]", key, v) for i, v in enumerate(node)]))
        else:
            yield path, key, node


def _short_path(path: str) -> str:
    return path if len(path) <= PATH_CHARS else "…" + path[-(PATH_CHARS - 1) :]


def _json_hits(name: str, value: Any, currency: str) -> Iterator[str]:
    for path, key, leaf in _leaves(value):
        where = _WS.sub(" ", f"{name} {_short_path(path)}").strip()
        if isinstance(leaf, str):
            if len(leaf) > MAX_LEAF_CHARS or not _DIGIT.search(leaf):
                continue
            text = _WS.sub(" ", leaf).strip()
            mine = [p for p in find_prices(text, [currency]) if p.currency == currency]
            if mine:
                offset = len(where) + 2
                yield _clip(f"{where}: {text}", offset + mine[0].start, offset + mine[0].end)
                continue
            priced = _JSON_PRICE_LINE.search(key) and _NUMERIC.fullmatch(text)
        else:
            priced = (
                _JSON_PRICE_LINE.search(key)
                and isinstance(leaf, int | float)
                and not isinstance(leaf, bool)
            )
        if priced:
            yield _clip(f"{where}: {leaf}")


def _text_hits(name: str, text: str, currency: str) -> Iterator[str]:
    """Hits in a blob that is not JSON (a JavaScript object literal): windows of text around
    amounts in `currency` and price-like `key: number` pairs, in its first MAX_SCAN_CHARS."""
    text = text[:MAX_SCAN_CHARS]
    spans = [(p.start, p.end) for p in find_prices(text, [currency]) if p.currency == currency]
    spans += [(m.start(), m.end()) for m in _PRICE_KV.finditer(text)]
    for start, end in sorted(spans):
        lo, hi = max(0, start - LINE_CHARS // 2), min(len(text), end + LINE_CHARS // 2)
        window = _WS.sub(" ", text[lo:hi]).strip()
        offset = len(name) + 2 + (start - lo)  # approximate once whitespace is collapsed
        yield _clip(f"{name}: {window}", offset, offset + (end - start))


def embedded_amount_snippets(html: str, currency: str) -> tuple[list[str], int]:
    """First MAX_EMBEDDED_SNIPPETS price-like values inside embedded data, and how many there
    are (counted up to MAX_EMBEDDED_HITS). Strings must carry an amount in `currency`; bare
    numbers count only under a price-like key ("price", "amount", "precio", ...)."""
    return _distinct_hits(
        found
        for name, body, is_json in _script_blobs(html)
        for found in _blob_hits(name, body, is_json, currency)
    )


def _blob_hits(name: str, body: str, is_json: bool, currency: str) -> Iterator[str]:
    body = body[:MAX_BLOB_CHARS]
    try:
        value = json.loads(body) if is_json else json.JSONDecoder().raw_decode(body.lstrip())[0]
    except (ValueError, RecursionError):  # not JSON, or nested deeper than the parser goes
        return _text_hits(name, body, currency)
    return _json_hits(name, value, currency)


def _distinct_hits(found: Iterator[str]) -> tuple[list[str], int]:
    hits: dict[str, None] = {}
    for hit in found:
        hits.setdefault(hit, None)
        if len(hits) >= MAX_EMBEDDED_HITS:
            break
    return list(hits)[:MAX_EMBEDDED_SNIPPETS], len(hits)


def is_pdf(resp: RawResponse) -> bool:
    """A PDF by its kept bytes, its content type, or its "%PDF-" signature (servers often say
    octet-stream)."""
    if resp.body or "pdf" in (resp.content_type or "").lower():
        return True
    return resp.text.lstrip(_PDF_LEAD).startswith("%PDF-")


def _is_login(url: str) -> bool:
    parts = urlsplit(url)
    return bool(_LOGIN_HOST.match(parts.hostname or "") or _LOGIN_PATH.search(parts.path))


def _login_wall(url: str, final_url: str) -> bool:
    """The final URL is a sign-in page the requested one was not (a login wall)."""
    return _is_login(final_url) and not _is_login(url)


def page_wall(headers: dict[str, str], title: str, lines: list[str], thin: bool) -> str | None:
    """The bot wall a 2xx page is: by a definite challenge header, or, on a thin page, by a
    marker in its title or visible text. Never by script sources or attributes: ordinary pages
    load reCAPTCHA, DataDome or Cloudflare's bot-management scripts."""
    definite = {k: v for k, v in headers.items() if k.lower() in _CHALLENGE_HEADERS}
    return looks_like_bot_wall(definite, "\n".join([title, *lines]) if thin else "")


def _country_segment(path: str, country: CountryDef) -> str | None:
    """The segment of a URL path that names `country` ("co", "es-co", "sv_se", apple.com's
    "uk"), if any."""
    cc = country.cc.lower()
    names = {cc, (country.apple_path or "").lower(), (country.disney_locale or "").lower()}
    for seg in path.lower().split("/"):
        if seg and (seg in names or re.fullmatch(rf"[a-z]{{2,3}}[-_]{cc}", seg)):
            return seg
    return None


# ============================================================================ per target


def _base_row(target: BundleTarget, catalog: Catalog, bundle: Bundle | None) -> dict[str, Any]:
    return {
        "label": target.label,
        "country": target.country,
        "currency": catalog.country(target.country).currency,
        "known_bundle": bundle is not None,
        "bundle_name": bundle.name if bundle else None,
        "url": target.url,
        "line": target.line,
        "status": None,
        "http_status": None,
        "final_url": None,
        "final_host": None,
        "same_site": None,
        # The country segment of the requested path ("co") that a same-site redirect dropped.
        "lost_country_segment": None,
        "content_type": None,
        "page_len": 0,
        "pdf": False,
        # Extraction status (ok, pdf_encrypted, pdf_no_text, pdf_limit, pdf_error; None when the
        # fetcher kept no bytes) and page count of a PDF.
        "pdf_status": None,
        "pdf_pages": 0,
        "text_chars": 0,
        "thin_page": False,
        "wall": None,
        "page_title": "",
        "json_script_ids": [],
        "amount_lines": [],
        "amount_lines_total": 0,
        "other_currencies": {},
        "component_lines": [],
        "embedded_amount_snippets": [],
        "embedded_amount_total": 0,
        "readability": None,
        "suggested_mode": None,
        "mode_reason": "",
        "notes": [],
        "raw_file": None,
        "error": None,
        "unexpected": False,
        "ms": 0,
    }


async def probe_target(
    target: BundleTarget,
    fetcher: Fetcher,
    catalog: Catalog,
    bundle: Bundle | None,
    raw_dir: Path,
    index: int,
) -> dict[str, Any]:
    row = _base_row(target, catalog, bundle)
    started = time.monotonic()
    try:
        resp = await _fetch_url(fetcher, target.url)
    except Exception as exc:
        row.update(_fetch_failure(exc))
    else:
        try:
            # Never raises for a bad PDF: the status says what happened.
            pdf = await extract_pdf_text(resp.body) if is_pdf(resp) and resp.body else None
            _inspect(row, target, resp, catalog, bundle, raw_dir, index, pdf)
        except RecursionError:
            # HTML nested thousands of levels deep: the page's doing, a finding and not a bug.
            row.update(
                status="failed",
                error="RecursionError: el HTML está anidado demasiado hondo para leer su texto",
            )
        except Exception as exc:  # the analysis is this mode's product: a crash is a bug
            row.update(status="error", error=_exc_text(exc), unexpected=True)
            row["notes"].append(traceback.format_exc(limit=4).strip()[-1500:])
    row["ms"] = int((time.monotonic() - started) * 1000)
    _classify(row)
    _limit_quotes(row)
    return row


def _inspect(
    row: dict[str, Any],
    target: BundleTarget,
    resp: RawResponse,
    catalog: Catalog,
    bundle: Bundle | None,
    raw_dir: Path,
    index: int,
    pdf: PdfText | None = None,
) -> None:
    final_host = host_of(resp.final_url)
    row.update(
        status="ok",
        http_status=resp.status_code,
        final_url=resp.final_url,
        final_host=final_host,
        same_site=site_of(final_host) == site_of(host_of(target.url)),
        content_type=resp.content_type,
        page_len=len(resp.text),
    )
    if resp.final_url.rstrip("/") != target.url.rstrip("/"):
        row["notes"].append("redirigida: la URL final no es la pedida")
        country = catalog.country(target.country)
        wanted = _country_segment(urlsplit(target.url).path, country)
        if wanted and _country_segment(urlsplit(resp.final_url).path, country) is None:
            row["lost_country_segment"] = wanted
    name = f"{index + 1:02d}_{_safe_name(target.label)}_{target.country}"
    if is_pdf(resp):
        # The decoded body of a PDF is binary garbage: only its extracted text is read.
        row["pdf"] = True
        if pdf is None:
            row["notes"].append("PDF sin bytes del fetcher: no hay texto que extraer ni citar")
            return
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{name}.pdf"
        raw_path.write_bytes(resp.body)
        row.update(
            page_len=len(resp.body),
            raw_file=f"raw/bundles/{raw_path.name}",
            pdf_status=pdf.status,
            pdf_pages=pdf.pages,
        )
        if not pdf.ok:
            row["notes"].append(f"PDF ilegible, detalle del lector: {pdf.reason}")
            return
        if pdf.detail:
            row["notes"].append(f"PDF: {pdf.detail}")
        _scan_lines(row, list(pdf.lines), catalog, bundle)
        if _login_wall(target.url, resp.final_url):
            row["wall"] = "login"
        else:
            row["wall"] = page_wall(resp.headers, "", [], False)
        return
    currency = row["currency"]
    is_json = "json" in (resp.content_type or "").lower()
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{name}.{'json' if is_json else 'html'}"
    raw_path.write_text(resp.text, encoding="utf-8")
    row["raw_file"] = f"raw/bundles/{raw_path.name}"
    lines = [] if is_json else page_lines(resp.text)
    row["thin_page"] = not is_json and sum(len(ln) for ln in lines) < THIN_TEXT_CHARS
    if not is_json:
        row["page_title"] = page_title(resp.text)
        # Page-controlled attribute text: one line each, clipped, and counted in QUOTE_BUDGET.
        ids = (_clip(_WS.sub(" ", i).strip(), width=PATH_CHARS) for i in json_script_ids(resp.text))
        row["json_script_ids"] = list(dict.fromkeys(ids))[:MAX_SCRIPT_IDS]
    _scan_lines(row, lines, catalog, bundle)
    if is_json:
        snippets, total = _distinct_hits(_blob_hits("body", resp.text, True, currency))
    else:
        snippets, total = embedded_amount_snippets(resp.text, currency)
    row["embedded_amount_snippets"] = snippets
    row["embedded_amount_total"] = total

    if _login_wall(target.url, resp.final_url):
        row["wall"] = "login"
    else:
        row["wall"] = page_wall(resp.headers, row["page_title"], lines, row["thin_page"])
    if row["thin_page"]:
        row["notes"].append(
            f"texto visible muy corto ({row['text_chars']} caracteres): probablemente se pinta "
            "con JavaScript"
        )


def _scan_lines(
    row: dict[str, Any], lines: list[str], catalog: Catalog, bundle: Bundle | None
) -> None:
    """Text size, amount lines, other currencies and component lines of the visible lines (an
    HTML page's rendered lines or a PDF's extracted ones)."""
    currency = row["currency"]
    row["text_chars"] = sum(len(ln) for ln in lines)
    scan = amount_lines(lines, currency)
    row["amount_lines_total"] = len(scan.lines)
    row["amount_lines"] = scan.lines[:MAX_AMOUNT_LINES]
    row["other_currencies"] = dict(sorted(scan.others.items()))
    if scan.below_floor:
        row["notes"].append(
            f'{scan.below_floor} importe(s) con "$" por debajo de {_DOLLAR_FLOOR[currency]} no se '
            f"cuentan como {currency}: probablemente están en otra moneda"
        )
    if bundle is not None:
        terms = component_terms(bundle, catalog)
        row["component_lines"] = component_lines(lines, terms, scan.source)[:MAX_COMPONENT_LINES]


def _classify(row: dict[str, Any]) -> None:
    """Derive readability and the suggested watch mode (auto only for `readable`)."""
    status = row["status"]
    if status in {"blocked", "robots_disallowed"}:
        readability = status
    elif status != "ok":
        readability = "failed"
    elif row["wall"]:
        # A wall decides alone, before the host and the amounts: what it shows (a sign-in page
        # on another host, a challenge with a price on it) is not the bundle page.
        readability = "blocked"
    elif not row["same_site"] or row["lost_country_segment"]:
        readability = "wrong_country"
    elif row["pdf"] and row["pdf_status"] is None:
        readability = "pdf_pending"
    elif row["pdf"] and row["pdf_status"] != "ok":
        readability = "pdf_unreadable"
    elif row["amount_lines_total"]:
        readability = "readable"
    elif row["other_currencies"]:
        readability = "wrong_country"
    elif row["embedded_amount_total"]:
        readability = "embedded_only"
    else:
        readability = "no_amounts"
    if row["wall"] and row["amount_lines_total"]:
        row["notes"].append(
            f"importes visibles detrás del muro ({row['wall']}): no fijan el modo; revisar a mano"
        )
    row["readability"] = readability
    row["suggested_mode"] = "auto" if readability == "readable" else "manual"
    row["mode_reason"] = _reason(readability, row)


def _reason(readability: str, row: dict[str, Any]) -> str:
    """Why this mode, in Spanish. Never a way around a wall: only what a human should check."""
    cur = row["currency"]
    if readability == "readable":
        return f"importes en {cur} en el texto visible"
    if readability == "embedded_only":
        return "los importes solo están en datos embebidos (JSON), que no fijan un precio solos"
    if readability == "no_amounts":
        thin = " (texto visible muy corto: probablemente JavaScript)" if row["thin_page"] else ""
        return f"sin importes en {cur} en la página{thin}"
    if readability == "pdf_pending":
        return "PDF: el fetcher no entregó sus bytes, así que no hay texto que leer"
    if readability == "pdf_unreadable":
        why = _PDF_UNREADABLE_ES.get(row["pdf_status"], "no se pudo leer")
        return f"PDF {why} ({row['pdf_status']}): comprobación manual"
    if readability == "wrong_country":
        if row["same_site"] is False:
            wanted = site_of(host_of(row["url"]))
            return f"el host final {row['final_host']} no es el sitio pedido ({wanted})"
        if row["lost_country_segment"]:
            seg = row["lost_country_segment"]
            return f"la redirección quitó el segmento de país /{seg}/ de la URL"
        others = ", ".join(row["other_currencies"])
        return f"solo hay importes en otras monedas ({others}), ninguno en {cur}"
    if readability == "blocked":
        wall = f"muro ({row['wall']})" if row["wall"] else "bloqueada por protección anti-bots"
        return f"{wall}: no se evade; comprobación manual"
    if readability == "robots_disallowed":
        return "robots.txt no permite esta URL a nuestro user agent: se respeta"
    return "la descarga o el análisis falló" + (f" ({row['error']})" if row["error"] else "")


# Why a PDF could not be read, by extraction status (haggler.fetch.pdf).
_PDF_UNREADABLE_ES = {
    "pdf_encrypted": "cifrado o sin permiso de extracción de texto: no se fuerza",
    "pdf_no_text": "sin capa de texto (probablemente escaneado)",
    "pdf_limit": "por encima de un tope (tamaño, páginas, texto, memoria o CPU)",
    "pdf_error": "dañado o ilegible para el lector",
}

_QUOTED_LISTS = ("amount_lines", "component_lines", "embedded_amount_snippets", "json_script_ids")


def _limit_quotes(row: dict[str, Any]) -> None:
    """Cap the page text a row quotes at QUOTE_BUDGET characters, spent in order on the title
    (at most TITLE_CHARS), the amount lines, the component lines, the embedded snippets and the
    JSON script ids. A line cut by the budget ends in "…"; once the budget is spent the rest is
    dropped."""
    left = QUOTE_BUDGET

    def take(text: str, cap: int = QUOTE_BUDGET) -> str:
        nonlocal left
        room = max(0, min(left, cap))
        kept = text if len(text) <= room else (text[: room - 1] + "…" if room > 1 else "")
        left -= len(kept)
        return kept

    row["page_title"] = take(row["page_title"], TITLE_CHARS)
    for key in _QUOTED_LISTS:
        kept = []
        for line in row[key]:
            if left <= 1:
                break
            kept.append(take(line))
        row[key] = kept


def quoted_chars(row: dict[str, Any]) -> int:
    """Verbatim page text a row quotes (what QUOTE_BUDGET bounds)."""
    return len(row["page_title"]) + sum(len(s) for k in _QUOTED_LISTS for s in row[k])


async def probe_bundle_targets(
    targets: list[BundleTarget],
    fetcher: Fetcher,
    catalog: Catalog,
    bundles: BundlesFile,
    out_dir: Path,
    *,
    concurrency: int = 8,
) -> list[dict[str, Any]]:
    """Hosts in parallel, each host's targets one after another (as in probe_adapters), so
    the per-target timeout is about the request, not about the fetcher's per-host queue."""
    known = {b.bundle_id: b for b in bundles.bundles}
    sem = asyncio.Semaphore(max(1, concurrency))
    by_host: dict[str, list[int]] = {}
    for i, t in enumerate(targets):
        by_host.setdefault(host_of(t.url) or f"#{i}", []).append(i)
    rows: dict[int, dict[str, Any]] = {}
    raw_dir = out_dir / "raw" / "bundles"

    async def worker(indices: list[int]) -> None:
        async with sem:
            for i in indices:
                t = targets[i]
                rows[i] = await probe_target(t, fetcher, catalog, known.get(t.label), raw_dir, i)

    await asyncio.gather(*(worker(ix) for ix in by_host.values()))
    return [rows[i] for i in range(len(targets))]


# ============================================================================ markdown


def _code(text: str) -> str:
    """`text` as an inline code span on one line: rendered verbatim (no Markdown, HTML or math),
    so a quote can be copied as an anchor. Whitespace runs (line breaks included, which would end
    the span) become one space; the fence is longer than any backtick run inside."""
    text = _WS.sub(" ", text)
    fence = "`" * (max((len(m) for m in _BACKTICKS.findall(text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _plain(text: str) -> str:
    """`text` as inline Markdown that renders literally: on one line, with every character that
    could start a link, HTML, an entity or emphasis backslash-escaped (reasons carry hosts and
    error messages from the vendor's responses)."""
    return _MD_SPECIAL.sub(r"\\\1", _WS.sub(" ", text))


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(r[key]) for r in rows).items()))


def render_bundles_md(rows: list[dict[str, Any]], *, generated_at: datetime) -> str:
    totals = _counts(rows, "readability")
    lines = [
        f"# Probe de paquetes ({generated_at:%Y-%m-%d %H:%M} UTC)",
        "",
        f"**{len(rows)} objetivos**: " + ", ".join(f"{k} {v}" for k, v in totals.items()),
        "",
        "Solo una página `readable` puede vigilarse en modo `auto`; las demás quedan en `manual` "
        "con su razón. Los muros, robots.txt y bloqueos se respetan: nunca se evaden.",
        "",
        "| Etiqueta | CC | Host | HTTP | Tipo | Legibilidad | Modo sugerido | Líneas con importe |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        cells = (
            row["label"],
            row["country"],
            row["final_host"] or host_of(row["url"]),
            row["http_status"],
            _code(row["content_type"]) if row["content_type"] else None,
            row["readability"],
            row["suggested_mode"],
            row["amount_lines_total"],
        )
        lines.append("| " + " | ".join(_cell(v) for v in cells) + " |")
    lines += ["", "## Detalles", ""]
    for n, row in enumerate(rows, 1):
        lines += _details(n, row)
    return "\n".join(lines) + "\n"


def _details(n: int, row: dict[str, Any]) -> list[str]:
    cur = row["currency"]
    out = [f"### {n}. {row['label']} ({row['country']}): {row['readability']}", ""]
    final = row["final_url"]
    redirect = f" -> {_code(final)}" if final and final != row["url"] else ""
    out.append(f"- URL: {_code(row['url'])}{redirect}")
    out.append(
        f"- Paquete conocido: {_code(row['bundle_name'])}"
        if row["known_bundle"]
        else "- Etiqueta libre (no está en data/curated/bundles.yaml)"
    )
    status = f"- Estado: {row['status']}"
    if row["http_status"] is not None:
        status += f", HTTP {row['http_status']}"
    if final:
        same = "sí" if row["same_site"] else "no"
        ctype = _code(row["content_type"]) if row["content_type"] else "—"
        if row["pdf"] and row["pdf_status"]:
            size = f"{row['page_len']} bytes (PDF {row['pdf_status']}: {row['pdf_pages']} "
            size += f"páginas, texto: {row['text_chars']})"
        elif row["pdf"]:
            size = f"{row['page_len']} caracteres (PDF sin bytes)"
        else:
            size = f"{row['page_len']} caracteres (texto visible: {row['text_chars']})"
        status += f", {ctype}, {size}, mismo sitio: {same}"
    out.append(status)
    if row["page_title"]:
        out.append(f"- Título: {_code(row['page_title'])}")
    other = ", ".join(f"{c} ×{k}" for c, k in row["other_currencies"].items())
    out.append(f"- Moneda esperada: {cur}" + (f"; otras en el texto: {other}" if other else ""))
    if row["wall"]:
        out.append(f"- Muro: {row['wall']}")
    if row["json_script_ids"]:
        out.append("- Scripts JSON: " + ", ".join(_code(i) for i in row["json_script_ids"]))
    out.append(f"- Modo sugerido: **{row['suggested_mode']}**: {_plain(row['mode_reason'])}")
    if row["error"]:
        out.append(f"- Error: {_code(_WS.sub(' ', row['error'])[:300])}")
    for note in row["notes"][:8]:
        out.append(f"- Nota: {_code(_WS.sub(' ', note)[:300])}")
    sections = (
        (
            f"Líneas con importe en {cur} ({len(row['amount_lines'])} de "
            f"{row['amount_lines_total']})",
            row["amount_lines"],
        ),
        ("Líneas que nombran el paquete o sus servicios", row["component_lines"]),
        (
            f"Importes en datos embebidos ({len(row['embedded_amount_snippets'])} de "
            f"{row['embedded_amount_total']}"
            + ("+" if row["embedded_amount_total"] >= MAX_EMBEDDED_HITS else "")
            + ")",
            row["embedded_amount_snippets"],
        ),
    )
    for title, quoted in sections:
        if quoted:
            out.append(f"- {title}:")
            out += [f"  - {_code(q)}" for q in quoted]
    out.append("")
    return out


# ============================================================================ entry point


def run(
    targets_file: Path,
    out_dir: Path,
    settings: Settings,
    *,
    catalog: Catalog,
    now: datetime,
    fetcher: Fetcher | None = None,
    bundles: BundlesFile | None = None,
) -> tuple[dict[str, Any], str]:
    """(summary json, markdown) for every target of `targets_file`; raises ValueError on a bad
    targets file before anything is fetched."""
    if bundles is None:
        bundles = load_bundles()
    targets = parse_bundle_targets(Path(targets_file).read_text(encoding="utf-8"), catalog, bundles)
    rows = asyncio.run(_probe(targets, fetcher, catalog, bundles, settings, Path(out_dir)))
    summary = {
        "mode": "bundles",
        "generated_at": now.astimezone(UTC).isoformat(),
        "targets_file": str(targets_file),
        "quote_budget": QUOTE_BUDGET,
        "status_totals": _counts(rows, "status"),
        "readability_totals": _counts(rows, "readability"),
        "targets": rows,
    }
    return summary, render_bundles_md(rows, generated_at=now)


async def _probe(
    targets: list[BundleTarget],
    fetcher: Fetcher | None,
    catalog: Catalog,
    bundles: BundlesFile,
    settings: Settings,
    out_dir: Path,
) -> list[dict[str, Any]]:
    owned = fetcher is None
    if fetcher is None:
        from haggler.fetch.client import HttpFetcher

        # robots.txt on every hop, identifiable user agent, per-host rate limit; default egress.
        fetcher = HttpFetcher(settings)
    try:
        return await probe_bundle_targets(
            targets, fetcher, catalog, bundles, out_dir, concurrency=settings.fetch_concurrency
        )
    finally:
        if owned:
            await fetcher.aclose()
