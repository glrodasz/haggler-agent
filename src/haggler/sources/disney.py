"""Disney+ prices from the marketing page www.disneyplus.com/{disney_locale}.

Plans appear as cards ("Disney+ Standard med reklam" / "69 kr/mån"), as a comparison table
(plan names as column heads, a "monthly price" row) and sometimes as JSON in scripts. Strategies
run in that order of trust: JSON, <table>, column-run lines, cards; one is chosen, never mixed.
Bundles (Hulu, ESPN+, Max, Combo+, Star+) are ignored, annual prices are skipped, and a
"Standard" label that mentions ads in an unknown language is moved to the ads plan.

A 404, an "unavailable" path or "not available in your region" text -> not_available; a
redirect to another locale segment -> wrong_country.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import ClassVar
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, ServiceDef
from haggler.models.run import Task
from haggler.sources.base import ParseResult, PerCountryAdapter, match_plan
from haggler.sources.spotify import (
    PlanHit,
    card_hits,
    column_runs,
    expected_currencies,
    json_documents,
    json_plan_hits,
    monthly_price,
    node_lines,
    page_lines,
    resolve,
    with_aliases,
)

# Plan names on Disney+ locale pages that catalog/services.yaml lacks (requested there).
EXTRA_ALIASES: dict[str, list[str]] = {
    "standard_with_ads": [
        "with adverts",
        "med reklame",
        "med reklamer",
        "mainoksilla",
        "mit werbung",
        "z reklamami",
        "s reklamami",
        "s reklamou",
        "reklámokkal",
        "hirdetésekkel",
        "cu reclame",
        "met reclame",
        "met advertenties",
        "com anúncios",
        "com anuncios",
        "con publicidad",
        "su reklamomis",
        "ar reklāmām",
        "reklaamidega",
        "standart reklamlı",
        "avec pub",
        "avec publicités",
    ],
    "standard": ["standaard", "standart", "padrão", "estandar"],
}
# Bundles and add-ons that reuse plan words ("Disney+, Hulu Bundle Basic", "Combo+ Premium").
_BUNDLE = re.compile(
    r"(?<!\w)(?:hulu|espn\+?|bundle|combo\+?|star\+|paket|paquete|pacote|trio|duo|max)(?!\w)"
    r"|\bdisney\+\s*,",
    re.IGNORECASE,
)
_ADS_HINT = re.compile(
    r"\b(ads?|adverts?|reklam\w*|werbung|anuncios?|anúncios?|publicid\w*|pubblicit\w*"
    r"|mainok\w*|mainos\w*|reclam\w*|hirdet\w*|reklám\w*|iklan|reklāmām|reklaam\w*)\b",
    re.IGNORECASE,
)
_NO_ADS = re.compile(
    r"\b(utan|without|no|sin|sem|ohne|uten|uden|ilman|bez|zonder|fără|nélkül|sans|reklamsız"
    r"|reklamfri\w*|reklamefri\w*|werbefrei)\b|ad[\s-]free",
    re.IGNORECASE,
)
_UNAVAILABLE_PATH = re.compile(r"unavailable|unsupported|not-available|notavailable")
_LOCALE = re.compile(r"[a-z]{2}-[a-z]{2}")


def _bundle_label(svc: ServiceDef, line: str) -> bool:
    """A bundle/add-on plan name (not a feature bullet that merely says "max")."""
    if len(line) > 80 or not _BUNDLE.search(line):
        return False
    return "disney+" in line.casefold() or match_plan(svc, line) is not None


def _fix_ads(hits: list[PlanHit]) -> list[PlanHit]:
    """ "Standard <ads word>" in a language the aliases miss is the ads plan, not Standard."""
    for h in hits:
        if h.plan_id == "standard" and _ADS_HINT.search(h.label) and not _NO_ADS.search(h.label):
            h.plan_id = "standard_with_ads"
    return hits


def _plan_of(svc: ServiceDef, text: str) -> str | None:
    if _bundle_label(svc, text):
        return None
    return match_plan(svc, text)


def table_hits(html: str, svc: ServiceDef, expected: list[str]) -> list[PlanHit]:
    """Comparison tables: a header row naming >=2 plans, then the first row priced per column."""
    hits: list[PlanHit] = []
    for table in LexborHTMLParser(html).css("table"):
        rows = [[" ".join(node_lines(c)) for c in tr.css("th, td")] for tr in table.css("tr")]
        head_i: int | None = None
        cols: dict[int, str] = {}
        for i, cells in enumerate(rows):
            mapped = {
                ci: pid
                for ci, text in enumerate(cells)
                if len(text) <= 60 and (pid := _plan_of(svc, text))
            }
            if len(set(mapped.values())) >= 2:
                head_i, cols = i, mapped
                break
        if head_i is None:
            continue
        width = len(rows[head_i])
        taken: set[str] = set()
        for cells in rows[head_i + 1 :]:
            offset = len(cells) - width
            if offset not in (0, 1):  # 1 = body rows carry a row-header cell the head lacks
                continue
            for ci, pid in cols.items():
                idx = ci + offset
                if pid in taken or idx >= len(cells):
                    continue
                money = monthly_price(cells[idx], expected)
                if money:
                    taken.add(pid)
                    hits.append(
                        PlanHit(
                            plan_id=pid,
                            label=rows[head_i][ci],
                            amount=money.amount,
                            currency=money.currency,
                            evidence=f"{rows[head_i][ci]}: {cells[idx]}",
                            strategy="table",
                        )
                    )
    return hits


def column_hits(lines: list[str], svc: ServiceDef, expected: list[str]) -> list[PlanHit]:
    """Grids rendered as divs: N plan names in a row, then their N monthly prices in order."""
    hits: list[PlanHit] = []
    for run in column_runs(lines, svc, expected, plan_of=lambda s: _plan_of(svc, s)):
        for (pid, label), (money, text) in zip(run.plans, run.prices, strict=True):
            hits.append(
                PlanHit(
                    plan_id=pid,
                    label=label,
                    amount=money.amount,
                    currency=money.currency,
                    evidence=f"{label}: {text}",
                    strategy="columns",
                )
            )
    return hits


class DisneyWebAdapter(PerCountryAdapter):
    id: ClassVar[str] = "disney_web"
    requires: ClassVar[tuple[str, ...]] = ("disney_locale",)

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        svc = with_aliases(cat.service(task.service_ids[0]), EXTRA_ALIASES)
        country = cat.country(task.countries[0])
        expected = expected_currencies(task, country)
        final = resp.final_url or resp.url
        path = urlsplit(final).path.lower()
        if resp.status_code in (404, 410) or _UNAVAILABLE_PATH.search(path):
            return ParseResult(
                status="not_available", notes=[f"HTTP {resp.status_code} at {final}"]
            )
        segs = [p for p in path.split("/") if p]
        want = (country.disney_locale or "").lower()
        if segs and _LOCALE.fullmatch(segs[0]) and segs[0] != want:
            return ParseResult(
                status="wrong_country", notes=[f"redirected to locale {segs[0]}: {final}"]
            )

        html = resp.text
        lines = page_lines(html)
        docs = json_documents(html)

        def bundle(text: str) -> bool:
            return _bundle_label(svc, text)

        def run(cur: list[str]) -> list[tuple[str, list[PlanHit]]]:
            strategies: list[tuple[str, Callable[[], list[PlanHit]]]] = [
                ("json", lambda: json_plan_hits(docs, svc, cur, reject=bundle)),
                ("table", lambda: table_hits(html, svc, cur)),
                ("columns", lambda: column_hits(lines, svc, cur)),
                ("cards", lambda: card_hits(lines, svc, cur, reject=bundle)),
            ]
            return [(name, _fix_ads(fn())) for name, fn in strategies]

        return resolve(task, svc, resp, lines, expected, run)
