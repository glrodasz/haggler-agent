"""Netflix prices from the Help Center article "Plans and Pricing".

URL: help.netflix.com/en/node/24926/{cc_lower}; the trailing country code picks the market
whose prices the article shows. Prices are printed one plan per line, "Standard with ads:
USD 7.99 / month" / "Standard: 169 kr / month"; lines about extra members ("Extra member with
ads: 49 kr / month each") are not plans. Labels map with match_plan (longest alias wins, so
"Standard with ads" beats "Standard"). If the article ignores the requested country and shows
the visitor's (IP-based) market instead, the prices come in a currency the URL country does not
use -> wrong_country.
"""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import urlsplit

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, ServiceDef
from haggler.models.run import Task
from haggler.money import find_prices
from haggler.sources.base import ParseResult, PerCountryAdapter, match_plan
from haggler.sources.heuristics import is_intro, price_period
from haggler.sources.spotify import PlanHit, card_hits, expected_currencies, resolve
from haggler.text import html_to_lines

_EXTRA = re.compile(
    r"\bextra\b|\badditional\b|\bextra-?medlem|\bmiembro\s+extra|\bmembro\s+extra"
    r"|\bzusatzmitglied",
    re.IGNORECASE,
)
_COLON = re.compile(r"[:：]")
_SENTENCE = re.compile(r"[.;!?•·|]\s")


def _url_country(url: str) -> str | None:
    """Country code at the end of /{lang}/node/{id}/{cc}, if the final URL still has one."""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if len(parts) >= 4 and parts[-3] == "node" and re.fullmatch(r"[A-Za-z]{2}", parts[-1]):
        return parts[-1].upper()
    return None


def colon_hits(lines: list[str], svc: ServiceDef, expected: list[str]) -> list[PlanHit]:
    """ "Label: price" pairs, also when several share one line (a <p> with <br>s collapsed)."""
    hits: list[PlanHit] = []
    for line in lines:
        if not _COLON.search(line):
            continue
        prices = find_prices(line, expected)
        prev = 0
        for k, m in enumerate(prices):
            seg = line[prev : m.start]
            prev = m.end
            parts = _COLON.split(seg)
            if len(parts) < 2:
                continue
            # Text between the previous price ("... / month") and this colon.
            label = _SENTENCE.split(parts[-2])[-1]
            label = re.sub(r"^\s*(?:/\s*[^\s\d]+\.?)?[\s,.\-–]*", "", label).strip()
            if not label or len(label) > 60 or _EXTRA.search(label):
                continue
            plan_id = match_plan(svc, label)
            if plan_id is None or m.amount <= 0 or is_intro(seg, line[m.end :]):
                continue
            nxt = prices[k + 1].start if k + 1 < len(prices) else len(line)
            hits.append(
                PlanHit(
                    plan_id=plan_id,
                    label=label,
                    amount=m.amount,
                    currency=m.currency,
                    evidence=line[max(0, m.start - 120) : m.end + 40],
                    strategy="colon",
                    period=price_period(line[m.end : min(nxt, m.end + 30)]) or "month",
                )
            )
    return hits


class NetflixHelpAdapter(PerCountryAdapter):
    id: ClassVar[str] = "netflix_help"

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        svc = cat.service(task.service_ids[0])
        country = cat.country(task.countries[0])
        expected = expected_currencies(task, country)
        final = resp.final_url or resp.url
        url_cc = _url_country(final)
        if url_cc and url_cc != country.cc:
            return ParseResult(
                status="wrong_country", notes=[f"final URL is for {url_cc}: {final}"]
            )
        lines = html_to_lines(resp.text, drop_chrome=True)

        def run(cur: list[str]) -> list[tuple[str, list[PlanHit]]]:
            return [
                ("colon", colon_hits(lines, svc, cur)),
                ("cards", card_hits(lines, svc, cur, reject=lambda s: bool(_EXTRA.search(s)))),
            ]

        result = resolve(task, svc, resp, lines, expected, run)
        if result.status == "no_prices" and resp.status_code in (404, 410):
            result.notes.append(f"HTTP {resp.status_code}: article moved or removed?")
        return result
