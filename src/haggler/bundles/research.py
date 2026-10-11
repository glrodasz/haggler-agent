"""What the bundle research agent works from, and what its value claims are checked against.

Pure (no ADK, no I/O): research.nodes builds a bundle subject's job, known pages, official
domains and value context from here, and the review side (milestone H8, proposals) reads
bundles.yaml values in the same vocabulary (research.fields, level "bundle").

* official_domains: the provider's domains for the BUNDLE'S country only (a page of another
  country never confirms a value: research.evidence source_class with path prefixes).
* known_pages: the official URL, the watch pages and, for a versioned PDF whose index links a
  newer edition (the watcher's PageCheck.newest_url), that newer document too.
* yaml_values: the bundle's current values as the vocabulary writes them ("21900 COP/month",
  "2026-12-31", "disney_plus.standard_with_ads", "hbo_max:30"), which the agent confirms or
  corrects; never anything the YAML does not state.
"""

from __future__ import annotations

from typing import Any

from haggler.models.bundles import BundleWatchState
from haggler.models.catalog import Catalog
from haggler.models.curated import Bundle, BundlesFile
from haggler.models.fx import FxTable
from haggler.money import format_money_code
from haggler.research.evidence import ValueContext

_DISCOUNT = "discount_pct"


def find_bundle(bundles: BundlesFile, bundle_id: str | None) -> Bundle | None:
    return next((b for b in bundles.bundles if b.bundle_id == bundle_id), None)


def official_domains(bundles: BundlesFile, bundle: Bundle) -> list[str]:
    provider = bundles.provider(bundle.provider_id)
    return list(provider.domains.get(bundle.country, [])) if provider else []


def known_pages(bundle: Bundle, watch: BundleWatchState | None = None) -> list[str]:
    urls = [str(bundle.official_url), *(str(p.url) for p in bundle.watch.pages)]
    if watch is not None:
        urls += [p.newest_url for p in watch.pages if p.newest_url]
    return list(dict.fromkeys(urls))


def yaml_values(bundle: Bundle) -> dict[str, Any]:
    """The bundle's values of bundles.yaml, by research field (only those it states)."""
    price = bundle.price
    out: dict[str, Any] = {}

    def money(field: str, amount: Any) -> None:
        if amount is not None:
            out[field] = format_money_code(amount, price.currency, price.period)

    money("bundle_price", price.amount)
    money("bundle_intro_price", price.intro_amount)
    if price.intro_months is not None:
        out["bundle_intro_months"] = str(price.intro_months)
    if price.intro_valid_until is not None:
        out["bundle_intro_until"] = price.intro_valid_until.isoformat()
    money("bundle_base_price", price.base_plan_amount)
    if price.contract_months is not None:
        out["bundle_contract_months"] = str(price.contract_months)
    if bundle.valid_until is not None:
        out["bundle_offer_until"] = bundle.valid_until.isoformat()
    included = [
        c.service_id + (f".{c.plan_id}" if c.plan_id else "")
        for c in bundle.components
        if c.inclusion == "included"
    ]
    if included:
        out["bundle_includes"] = included
    discounts = [
        f"{c.service_id}:{format(c.value.normalize(), 'f')}"
        for c in bundle.components
        if c.inclusion == _DISCOUNT and c.value is not None
    ]
    if discounts:
        out["bundle_discount"] = discounts
    pdfs = [str(p.url) for p in bundle.watch.pages if p.kind == "pdf"]
    if pdfs:
        out["bundle_price_list_url"] = pdfs[0]
    return out


def value_context(
    bundles: BundlesFile, bundle: Bundle, catalog: Catalog | None, fx: FxTable | None
) -> ValueContext:
    return ValueContext(
        bundle=bundle,
        provider=bundles.provider(bundle.provider_id),
        bundles=tuple(bundles.bundles),
        catalog=catalog,
        fx=fx,
    )


def job(
    bundles: BundlesFile,
    bundle: Bundle,
    catalog: Catalog,
    watch: BundleWatchState | None = None,
) -> dict[str, Any]:
    """The agent's job for one bundle subject (research.nodes.payload adds the subject key)."""
    provider = bundles.provider(bundle.provider_id)
    try:
        country_name = catalog.country(bundle.country).name_en
    except KeyError:
        country_name = bundle.country

    def service_name(service_id: str) -> str:
        try:
            return catalog.service(service_id).name
        except KeyError:
            return service_id

    return {
        "bundle_id": bundle.bundle_id,
        "name": bundle.name,
        "tier": bundle.tier,
        "aliases": list(bundle.aliases),
        "provider": provider.name if provider else bundle.provider_id,
        "country": bundle.country,
        "country_name": country_name,
        "currency": bundle.price.currency,
        "official_url": str(bundle.official_url),
        "official_domains": official_domains(bundles, bundle),
        "known_pages": known_pages(bundle, watch),
        "components": [
            {
                "service_id": c.service_id,
                "service": service_name(c.service_id),
                "plan_id": c.plan_id,
                "inclusion": c.inclusion,
                "value": format(c.value.normalize(), "f") if c.value is not None else None,
            }
            for c in bundle.components
        ],
        "requirements": bundle.requirements.model_dump(mode="json"),
        "yaml_values": yaml_values(bundle),
        "service_ids": {s.id: s.name for s in catalog.services},
    }
