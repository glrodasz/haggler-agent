"""Adapter contract.

An adapter turns one (service, source) definition into fetch Tasks and parses each fetched page
into ObservationDrafts. `parse` must be pure (no I/O) so it is testable with fixtures.

Typical subclass:

    class SpotifyAdapter(PerCountryAdapter):
        id = "spotify_premium"
        def parse(self, task, resp, cat):
            ...
            return ParseResult(observations=[...], status="ok")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar, Literal, Protocol
from urllib.parse import urlsplit

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, CountryDef, ServiceDef, SourceDef
from haggler.models.observations import BillingPeriod, ObservationDraft
from haggler.models.run import Task

ParseStatus = Literal["ok", "partial", "no_prices", "wrong_country", "not_available", "blocked"]


@dataclass
class IapItem:
    """One App Store in-app purchase row (name + price text as listed)."""

    name: str
    price_text: str
    amount: Decimal | None = None
    currency: str | None = None


@dataclass
class LlmRequest:
    """Adapter asks the pipeline for LLM help on one (service, country)."""

    service_id: str
    country: str
    reason: Literal["parser_failed", "unmapped_iap"]
    page_text: str


@dataclass
class ParseResult:
    observations: list[ObservationDraft] = field(default_factory=list)
    status: ParseStatus = "ok"
    missing_plans: list[str] = field(default_factory=list)
    unmapped: list[IapItem] = field(default_factory=list)
    llm_requests: list[LlmRequest] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Adapter(Protocol):
    id: ClassVar[str]

    def plan(self, svc: ServiceDef, src: SourceDef, cat: Catalog) -> list[Task]: ...

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult: ...


class AdapterNotConfigured(Exception):
    """The country lacks the catalog fields this adapter needs (e.g. apple_path is null)."""


def url_vars(country: CountryDef) -> dict[str, str]:
    apple_prefix = ""
    if country.apple_path:
        apple_prefix = f"{country.apple_path}/"
    return {
        "cc": country.cc,
        "cc_lower": country.cc.lower(),
        "apple_prefix": apple_prefix,
        "apple_path": country.apple_path or "",
        "disney_locale": country.disney_locale or "",
    }


def render_url(template: str, country: CountryDef | None, **extra: object) -> str:
    values: dict[str, object] = dict(url_vars(country)) if country else {}
    values.update(extra)
    return template.format(**values)


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def make_task_id(adapter_id: str, service_id: str, scope: str) -> str:
    return re.sub(r"[^A-Za-z0-9_:.-]", "_", f"{adapter_id}:{service_id}:{scope}")


class PerCountryAdapter:
    """Default planning: one task per applicable country, URL from params['url']."""

    id: ClassVar[str] = "base"
    # Country fields that must be non-null for the URL template; missing -> not_configured.
    requires: ClassVar[tuple[str, ...]] = ()

    def plan(self, svc: ServiceDef, src: SourceDef, cat: Catalog) -> list[Task]:
        tasks: list[Task] = []
        for country in cat.countries:
            if not src.applies_to(country.cc):
                continue
            missing = [f for f in self.requires if getattr(country, f) is None]
            url = "" if missing else render_url(src.params["url"], country, **self.extra_vars(src))
            tasks.append(
                Task(
                    task_id=make_task_id(self.id, svc.id, country.cc),
                    adapter_id=self.id,
                    service_ids=[svc.id],
                    countries=[country.cc],
                    channel=src.channel,
                    kind=src.kind,
                    url=url,
                    host=host_of(url) if url else "",
                    params={**src.params, "not_configured": missing} if missing else src.params,
                )
            )
        return tasks

    def extra_vars(self, src: SourceDef) -> dict[str, object]:
        return {}

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:  # pragma: no cover
        raise NotImplementedError


def draft(
    task: Task,
    *,
    plan_id: str,
    label: str,
    amount: Decimal,
    currency: str,
    period: BillingPeriod = "month",
    evidence: str = "",
    country: str | None = None,
    service_id: str | None = None,
    flags: list[str] | None = None,
) -> ObservationDraft:
    return ObservationDraft(
        service_id=service_id or task.service_ids[0],
        plan_id=plan_id,
        plan_label_raw=label.strip()[:120],
        country=country or task.countries[0],
        currency=currency,
        amount=amount,
        billing_period=period,
        evidence_snippet=evidence.strip()[:240],
        flags=flags or [],
    )


def match_plan(svc: ServiceDef, label: str) -> str | None:
    """Map a web-page plan label to a plan id using PlanDef.aliases (longest alias wins)."""
    norm = " ".join(label.casefold().split())
    best: tuple[int, str] | None = None
    for plan in svc.plans:
        for alias in plan.aliases:
            a = alias.casefold()
            if re.search(rf"(?<![\w]){re.escape(a)}(?![\w])", norm) and (
                best is None or len(a) > best[0]
            ):
                best = (len(a), plan.id)
    return best[1] if best else None


def match_iap_plan(svc: ServiceDef, name: str) -> list[str]:
    """Plan ids whose iap_patterns match an in-app purchase name (exactly one is expected)."""
    return [
        p.id
        for p in svc.plans
        if any(re.search(pat, name, re.IGNORECASE) for pat in p.iap_patterns)
    ]
