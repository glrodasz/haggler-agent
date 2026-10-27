"""Amazon Prime membership prices from each country's Amazon store (amazon.de/amazonprime, ...).

Prime Video has no in-app subscription on the App Store and primevideo.com prices follow the
visitor's IP, so the catalog records the Amazon Prime membership, which includes Prime Video,
from the store of each country that has one (params.domains {CC: host}; params.paths {CC: path}
overrides the default /amazonprime path). The page states the
regular price in a sentence such as "After, Prime is just €8.99 per month." / "Prime kosztuje
69 zł za rok."; it also advertises discounted memberships (Prime Student, Young Adults, Surface
trials, government assistance) at lower prices. Those lines print the regular price struck next
to the discounted one ("just $14.99 $7.49 per month") or name the programme, so only lines with
exactly one price and no programme word count, and the highest monthly and yearly price wins.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import ClassVar
from urllib.parse import urlsplit

from haggler.fetch import RawResponse
from haggler.fetch.walls import THIN_TEXT_CHARS, page_wall
from haggler.models.catalog import Catalog, ServiceDef, SourceDef
from haggler.models.observations import BillingPeriod, ObservationDraft
from haggler.models.run import Task
from haggler.money import find_prices
from haggler.sources.base import ParseResult, PerCountryAdapter, draft, host_of, make_task_id
from haggler.sources.heuristics import period_marker
from haggler.text import html_to_lines, page_title

PLAN_ID = "prime"
# Discounted memberships and partner offers advertised on the same page.
_DISCOUNTED = re.compile(
    r"student|estudiant|estudant|[ée]tudiant|studenc|young|jóvenes|jovens|giovani|jongeren"
    r"|surface|government|assistance|ebt|medicaid|courtesy|half|mitad|metade|moiti|metà"
    r"|delivery|entrega|livraison|lieferung|consegna|dostaw|leverans|grubhub|dashpass|grocery",
    re.IGNORECASE,
)
_PRIME = re.compile(r"\bprime\b", re.IGNORECASE)


class AmazonPrimeAdapter(PerCountryAdapter):
    id: ClassVar[str] = "amazon_prime"

    def plan(self, svc: ServiceDef, src: SourceDef, cat: Catalog) -> list[Task]:
        domains: dict[str, str] = src.params.get("domains") or {}
        tasks: list[Task] = []
        for country in cat.countries:
            host = domains.get(country.cc)
            if not host or not src.applies_to(country.cc):
                continue
            path = (src.params.get("paths") or {}).get(country.cc, "amazonprime")
            url = src.params["url"].format(host=host, path=path)
            tasks.append(
                Task(
                    task_id=make_task_id(self.id, svc.id, country.cc),
                    adapter_id=self.id,
                    service_ids=[svc.id],
                    countries=[country.cc],
                    channel=src.channel,
                    kind=src.kind,
                    url=url,
                    host=host_of(url),
                    params=src.params,
                )
            )
        return tasks

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        country = cat.country(task.countries[0])
        expected = list(country.expected_currencies(task.channel))
        final_host = urlsplit(resp.final_url or task.url).hostname or ""
        if final_host.removeprefix("www.") != task.host.removeprefix("www."):
            return ParseResult(
                status="wrong_country", notes=[f"redirected to {final_host}, not {task.host}"]
            )
        lines = html_to_lines(resp.text)
        thin = sum(len(ln) for ln in lines) < THIN_TEXT_CHARS
        if wall := page_wall(resp.headers, page_title(resp.text), lines, thin):
            return ParseResult(status="blocked", notes=[f"bot wall: {wall}"])
        best: dict[BillingPeriod, tuple[Decimal, str, str]] = {}
        foreign = 0
        for line in lines:
            if not _PRIME.search(line) or _DISCOUNTED.search(line):
                continue
            prices = find_prices(line, expected)
            if len(prices) != 1:
                continue
            price = prices[0]
            if price.currency not in expected:
                foreign += 1
                continue
            period = period_marker(line[price.end :])
            if period not in ("month", "year"):
                continue
            if period not in best or price.amount > best[period][0]:
                best[period] = (price.amount, price.currency, line)
        observations: list[ObservationDraft] = [
            draft(
                task,
                plan_id=PLAN_ID,
                label="Amazon Prime",
                amount=amount,
                currency=currency,
                period=period,
                evidence=line,
            )
            for period, (amount, currency, line) in sorted(best.items())
        ]
        if observations:
            return ParseResult(observations=observations, status="ok")
        if foreign:
            return ParseResult(
                status="wrong_country", notes=[f"prices only in other currencies ({foreign})"]
            )
        return ParseResult(status="no_prices", notes=["no regular Prime price sentence found"])
