"""bundles.yaml v2 (phase 3, milestone H1): the model, its integrity rules and the migration.

Every integrity rule has one negative case, built from a small valid file (BASE) so the cases do
not change when the real bundles.yaml is edited; the real file and the publish fixture are
checked as they are.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from haggler.catalog import (
    integrity_errors,
    load_bundles,
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
)
from haggler.feasibility import axes_rank, record_rank
from haggler.models.curated import (
    BundleRequirements,
    BundlesFile,
    FeasibilityStep,
)

PUBLISH_FIXTURES = Path(__file__).parents[1] / "publish" / "fixtures"
PAGE = "https://www.acme.com.co/planes"
INDEX = "https://www.acme.com.co/precios"
PDF = "https://cdn.acme.com.co/precios_12.pdf"
WAVE_1 = {
    "meli_plus_co_total",
    "meli_plus_co_esencial",
    "telia_se_obegransad_streaming",
    "apple_one_individual_se",
    "apple_one_family_se",
}

BASE: dict[str, Any] = {
    "providers": [
        {
            "id": "acme",
            "name": "Acme",
            "domains": {"CO": ["acme.com.co"], "MX": ["acme.com.mx"]},
        }
    ],
    "bundles": [
        {
            "bundle_id": "acme_co_total",
            "provider_id": "acme",
            "provider_type": "marketplace",
            "country": "CO",
            "name": "Acme Total",
            "tier": "total",
            "aliases": ["Acme Total"],
            "official_url": PAGE,
            "price": {"amount": "24900", "currency": "COP", "tax_included": True},
            "components": [
                {"service_id": "netflix", "plan_id": "standard_with_ads", "inclusion": "included"},
                {"service_id": "hbo_max", "inclusion": "discount_pct", "value": "30"},
            ],
            "verified_by": "official_fetch",
            "watch": {
                "mode": "auto",
                "pages": [
                    {"url": PAGE, "kind": "html"},
                    {
                        "url": PDF,
                        "kind": "pdf",
                        "index_url": INDEX,
                        "link_pattern": r"precios_(?P<edition>\d+)\.pdf",
                    },
                ],
                "anchors": [
                    {
                        "fact": "price",
                        "url": PAGE,
                        "text": "Acme Total: $ 24.900/mes, envío gratis",
                    },
                    {
                        "fact": "component",
                        "service_id": "netflix",
                        "url": PDF,
                        "text": "Incluye Netflix Estándar con anuncios",
                    },
                ],
            },
            "verdicts": [
                {
                    "profile": "se_resident_co_citizen",
                    "legality": "lawful",
                    "tos": "breach_unenforced",
                    "practical": "remote",
                    "last_verified": "2026-10-08",
                }
            ],
            "last_verified": "2026-10-08",
        }
    ],
}


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


@pytest.fixture(scope="module")
def profiles():
    return load_profiles()


@pytest.fixture(scope="module")
def instruments():
    return load_payment_instruments()


def _errors(data: dict, cat, profiles, instruments) -> list[str]:
    return integrity_errors(
        cat,
        bundles=BundlesFile.model_validate(data),
        instruments=instruments,
        profiles=profiles,
    )


def _mutated(mutate: Callable[[dict, dict], Any]) -> dict:
    data = copy.deepcopy(BASE)
    mutate(data, data["bundles"][0])
    return data


def _second_bundle(data: dict, bundle_id: str = "acme_co_esencial", **changes: Any) -> dict:
    other = copy.deepcopy(data["bundles"][0])
    other.update(bundle_id=bundle_id, name="Acme Esencial", tier="esencial", **changes)
    other["aliases"] = ["Acme Esencial"]
    other["verdicts"] = []
    data["bundles"].append(other)
    return other


def _set(path: str, value: Any) -> Callable[[dict, dict], None]:
    """Set bundle[a][b]...= value for a dotted path (integers index lists)."""

    def mutate(_data: dict, bundle: dict) -> None:
        *parents, last = path.split(".")
        node: Any = bundle
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        node[int(last) if last.isdigit() else last] = value

    return mutate


def test_base_is_valid(cat, profiles, instruments) -> None:
    assert _errors(BASE, cat, profiles, instruments) == []


# ------------------------------------------------------------- integrity rules


def _shared_url_without_aliases(data: dict, _b: dict) -> None:
    _second_bundle(data)["aliases"] = []


def _shared_url_same_alias(data: dict, _b: dict) -> None:
    _second_bundle(data)["aliases"] = ["ACME TOTAL"]


def _duplicate_verdict(_data: dict, b: dict) -> None:
    b["verdicts"].append(copy.deepcopy(b["verdicts"][0]))


def _choose_n(n_first: int, n_second: int) -> Callable[[dict, dict], None]:
    def mutate(_data: dict, b: dict) -> None:
        b["components"] += [
            {"service_id": "vix", "inclusion": "choose_n", "choice_group_id": "g", "choose_n": n}
            for n in (n_first, n_second)
        ]

    return mutate


def _forbidden_provider(data: dict, _b: dict) -> None:
    data["providers"][0]["automated_access"] = "forbidden"


def _provider_domain(cc: str, domains: list[str]) -> Callable[[dict, dict], None]:
    def mutate(data: dict, _b: dict) -> None:
        data["providers"][0]["domains"][cc] = domains

    return mutate


def _duplicate_provider(data: dict, _b: dict) -> None:
    data["providers"].append(copy.deepcopy(data["providers"][0]))


def _on_shared_host(data: dict, b: dict) -> None:
    """Acme serves CO and MX from one host under /co/ and /mx/ (as apple.com does), so its CO
    domains carry the path prefix."""
    data["providers"][0]["domains"] = {
        "CO": ["www.acme.com/co/", "cdn.acme.com.co"],
        "MX": ["www.acme.com/mx/"],
    }
    shared = {PAGE: "https://www.acme.com/co/planes", INDEX: "https://www.acme.com/co/precios"}
    b["official_url"] = shared[PAGE]
    for item in [*b["watch"]["pages"], *b["watch"]["anchors"]]:
        for key in ("url", "index_url"):
            if item.get(key) in shared:
                item[key] = shared[item[key]]


def _then(*mutations: Callable[[dict, dict], Any]) -> Callable[[dict, dict], None]:
    def mutate(data: dict, b: dict) -> None:
        for m in mutations:
            m(data, b)

    return mutate


def _observed_price(plan_id: str) -> dict:
    return {
        "currency": "COP",
        "tax_included": True,
        "source": "observed",
        "observed_service_id": "apple_one",
        "observed_plan_id": plan_id,
    }


def _anchor(fact: str, text: str, **extra: Any) -> Callable[[dict, dict], None]:
    def mutate(_data: dict, b: dict) -> None:
        b["watch"]["anchors"].append({"fact": fact, "url": PAGE, "text": text, **extra})

    return mutate


def _base_plan(amount: str = "19900") -> Callable[[dict, dict], None]:
    def mutate(_data: dict, b: dict) -> None:
        b["price"].update(base_plan_name="Acme Base", base_plan_amount=amount)

    return mutate


def _esencial_tier(data: dict, _b: dict) -> None:
    """A second Acme tier in CO, with its own price and a valid price anchor."""
    other = _second_bundle(data, official_url="https://www.acme.com.co/esencial")
    other["price"] = {"amount": "9900", "currency": "COP", "tax_included": True}
    other["watch"]["pages"][0]["url"] = other["official_url"]
    other["watch"]["anchors"] = [
        {
            "fact": "price",
            "url": other["official_url"],
            "text": "Acme Esencial: $ 9.900/mes, envío",
        },
        {
            "fact": "component",
            "service_id": "netflix",
            "url": PDF,
            "text": "Incluye Netflix con anuncios",
        },
    ]


def _in_sweden(data: dict, b: dict) -> None:
    """The same bundle sold in SE, where the verdict's profile lives."""
    data["providers"][0]["domains"]["SE"] = ["acme.se"]
    b.update(country="SE", official_url="https://www.acme.se/planes", watch={"mode": "off"})
    b["price"]["currency"] = "SEK"
    b["verified_by"] = "search_summary"


CASES: list[tuple[str, Callable[[dict, dict], Any], str]] = [
    # provider and domains
    ("unknown provider", _set("provider_id", "nope"), "unknown provider nope"),
    (
        "official_url on another country's domain",
        _set("official_url", "https://www.acme.com.mx/planes"),
        "official_url 'https://www.acme.com.mx/planes' is not https on a CO domain",
    ),
    (
        "watch page over http",
        _set("watch.pages.0.url", "http://www.acme.com.co/planes"),
        "watch page 'http://www.acme.com.co/planes' is not https",
    ),
    (
        "index_url off the provider's domains",
        _set("watch.pages.1.index_url", "https://acme-precios.example.org/"),
        "watch index_url 'https://acme-precios.example.org/' is not https on a CO domain",
    ),
    (
        "anchor url off the provider's domains",
        _set("watch.anchors.0.url", "https://www.acme.com.mx/planes"),
        "anchor url 'https://www.acme.com.mx/planes' is not https on a CO domain",
    ),
    (
        "page of another country on a shared host",
        _then(_on_shared_host, _set("watch.pages.0.url", "https://www.acme.com/mx/planes")),
        "watch page 'https://www.acme.com/mx/planes' is not https on a CO domain",
    ),
    (
        "page outside every country path on a shared host",
        _then(_on_shared_host, _set("official_url", "https://www.acme.com/planes")),
        "official_url 'https://www.acme.com/planes' is not https on a CO domain",
    ),
    (
        "path prefix that is only a string prefix",
        _then(_on_shared_host, _set("official_url", "https://www.acme.com/colombia/planes")),
        "official_url 'https://www.acme.com/colombia/planes' is not https on a CO domain",
    ),
    ("provider country unknown", _provider_domain("XX", ["acme.xx"]), "unknown country XX"),
    ("provider bad domain", _provider_domain("MX", ["https://acme.mx"]), "bad official domain"),
    ("provider IP host", _provider_domain("MX", ["10.0.0.1"]), "is not a public host"),
    ("provider IP host with a path", _provider_domain("MX", ["10.0.0.1/mx/"]), "public host"),
    (
        "path prefix without the closing slash",
        _provider_domain("MX", ["www.acme.com/mx"]),
        "bad path prefix '/mx'",
    ),
    ("provider without domains", _provider_domain("MX", []), "provider acme/MX: no domains"),
    ("duplicate provider", _duplicate_provider, "duplicate provider id"),
    ("link_pattern does not compile", _set("watch.pages.1.link_pattern", "(?P<edition"), "bad"),
    (
        "link_pattern without the edition group",
        _set("watch.pages.1.link_pattern", r"precios_(\d+)\.pdf"),
        "has no group 'edition'",
    ),
    (
        "link_pattern that does not match its own url",
        _set("watch.pages.1.link_pattern", r"tarifas_(?P<edition>\d+)\.pdf"),
        f"link_pattern does not match its own url {PDF}",
    ),
    (
        "link_pattern without index_url",
        _set("watch.pages.1.index_url", None),
        "needs an index_url",
    ),
    ("watch auto without pages", _set("watch", {"mode": "auto"}), "needs at least one page"),
    ("forbidden provider watched", _forbidden_provider, "forbids automated access"),
    # anchors
    ("anchor too short", _set("watch.anchors.0.text", "$ 24.900/mes"), "between 20 and 400"),
    ("anchor too long", _set("watch.anchors.0.text", "Acme Total " * 40), "between 20 and 400"),
    (
        "anchor with three dots",
        _set("watch.anchors.0.text", "Acme Total: $ 24.900/mes, envío..."),
        "ellipsis",
    ),
    (
        "anchor with an ellipsis character",
        _set("watch.anchors.0.text", "…Acme Total: $ 24.900/mes, envío gratis"),
        "ellipsis",
    ),
    (
        "anchor on a page that is not watched",
        _set("watch.anchors.0.url", "https://www.acme.com.co/otra"),
        "is not one of the watch pages",
    ),
    (
        "component anchor without a component service",
        _set("watch.anchors.1.service_id", "spotify"),
        "component anchor needs the service_id of a component",
    ),
    # anchor values (offline check_value on the anchor text alone)
    (
        "price anchor with another amount",
        _set("watch.anchors.0.text", "Acme Total: $ 21.900/mes, envío gratis"),
        "price anchor does not state 24900 COP/month: the quote does not state 24900 COP",
    ),
    (
        "price anchor quoting a comparison price",
        _set("watch.anchors.0.text", "Acme Total: antes $ 24.900/mes, ahora $ 19.900/mes"),
        "is a comparison (struck-through) price, not the regular price",
    ),
    (
        "price anchor per year for a monthly price",
        _set("watch.anchors.0.text", "Acme Total: $ 24.900 al año, envío gratis"),
        "is per year, not per month",
    ),
    (
        "price anchor in another currency",
        _set("watch.anchors.0.text", "Acme Total: US$ 24.900/mes, envío gratis"),
        "'US$ 24.900' is USD",
    ),
    (
        "price anchor without the tier name when the provider has two tiers",
        _then(_esencial_tier, _set("watch.anchors.0.text", "Plan mensual: $ 24.900/mes, envío")),
        "acme_co_total: price anchor does not state 24900 COP/month: the quote names no tier",
    ),
    (
        "price anchor naming the sibling tier",
        _then(_esencial_tier, _set("watch.anchors.0.text", "Acme Esencial: $ 24.900/mes, envío")),
        "the tier named next to the amount is 'Acme Esencial' (acme_co_esencial)",
    ),
    (
        "base_price anchor without base_plan_amount",
        _anchor("base_price", "Acme Base: $ 19.900/mes, sin permanencia"),
        "base_price anchor needs price.base_plan_amount to confirm",
    ),
    (
        "base_price anchor with another amount",
        _then(_base_plan(), _anchor("base_price", "Acme Base: $ 18.900/mes, sin permanencia")),
        "base_price anchor does not state 19900 COP/month",
    ),
    (
        "intro anchor without intro_amount",
        _anchor("intro", "Acme Total: $ 9.900/mes por 3 meses"),
        "intro anchor needs price.intro_amount to confirm",
    ),
    (
        "intro anchor quoting a regular price",
        _then(
            _set("price.intro_amount", "9900"),
            _anchor("intro", "Acme Total: $ 9.900/mes, envío gratis"),
        ),
        "is the regular price, not an introductory price",
    ),
    (
        "component anchor that does not name the service",
        _set("watch.anchors.1.text", "Incluye una plataforma de series con anuncios"),
        "component anchor does not state netflix.standard_with_ads: the quote does not name",
    ),
    (
        "component anchor naming another plan",
        _set("watch.anchors.1.text", "Incluye Netflix Premium, sin costo adicional"),
        "names Netflix premium, not standard_with_ads",
    ),
    (
        "discount anchor without its number",
        _anchor("component", "20% OFF en HBO Max y otras plataformas", service_id="hbo_max"),
        "component anchor does not state hbo_max:30",
    ),
    (
        "validity anchor without a year",
        _anchor("validity", "Oferta válida hasta el 31 de diciembre"),
        "validity anchor states no date with a year",
    ),
    (
        "validity anchor with another date than valid_until",
        _then(
            _set("valid_until", "2026-12-31"),
            _anchor("validity", "Oferta válida hasta el 30 de noviembre de 2026"),
        ),
        "validity anchor does not state 2026-12-31",
    ),
    (
        "validity anchor with another start date than valid_from",
        _then(
            _set("valid_from", "2026-04-01"),
            _anchor("validity", "Erbjudandet gäller fr.o.m. 1 maj 2025, för nya kunder"),
        ),
        "validity anchor does not state 2026-04-01",
    ),
    # price and components
    (
        "marginal is not amount - base",
        _set(
            "price",
            {
                "amount": "699",
                "currency": "COP",
                "base_plan_name": "Acme Base",
                "base_plan_amount": "569",
                "marginal_amount": "120",
            },
        ),
        "marginal_amount 120 is not amount 699 - base_plan_amount 569",
    ),
    (
        "marginal without a base plan amount",
        _set("price", {"amount": "699", "currency": "COP", "marginal_amount": "130"}),
        "marginal_amount needs the base_plan_amount",
    ),
    (
        "base plan amount without its name",
        _set(
            "price",
            {
                "amount": "699",
                "currency": "COP",
                "base_plan_amount": "569",
                "marginal_amount": "130",
            },
        ),
        "base_plan_amount needs the base_plan_name",
    ),
    (
        "observed price with an amount",
        _set(
            "price",
            {
                "amount": "195",
                "currency": "COP",
                "source": "observed",
                "observed_service_id": "apple_one",
                "observed_plan_id": "individual",
            },
        ),
        "observed price takes its amount from the daily data",
    ),
    (
        "observed price of an unknown plan",
        _set("price", _observed_price("nope")),
        "observed price points to unknown plan",
    ),
    ("curated price without amount", _set("price.amount", None), "curated price needs an amount"),
    ("price without tax_included", _set("price.tax_included", None), "price needs tax_included"),
    ("price in another currency", _set("price.currency", "USD"), "USD is not CO's COP"),
    (
        "component of an unknown plan",
        _set("components.0.plan_id", "nope"),
        "unknown component netflix.nope",
    ),
    ("discount without value", _set("components.1.value", None), "needs a value > 0"),
    (
        "free months without value",
        _set("components.1", {"service_id": "vix", "inclusion": "free_months"}),
        "free_months component vix needs a value > 0",
    ),
    ("discount over 100 %", _set("components.1.value", "130"), "is over 100"),
    (
        "choose_n without group",
        _set("components.1", {"service_id": "vix", "inclusion": "choose_n", "choose_n": 1}),
        "needs choice_group_id and choose_n",
    ),
    ("choose_n larger than the group", _choose_n(3, 3), "has 2 options, choose_n 3"),
    ("choose_n differs within the group", _choose_n(1, 2), "different choose_n values"),
    # state and verification
    ("official_fetch without price anchor", _set("watch.anchors", []), "needs a price anchor"),
    (
        "official_fetch without component anchor",
        _set("watch.anchors.1.service_id", "hbo_max"),
        "needs a component anchor for netflix",
    ),
    ("user_report without report", _set("verified_by", "user_report"), "needs user_report"),
    (
        "valid_until before valid_from",
        lambda _d, b: b.update(valid_from="2026-10-01", valid_until="2026-09-01"),
        "is before valid_from",
    ),
    ("covers an unknown service", _set("covers", ["nope"]), "covers unknown service nope"),
    # identity
    (
        "duplicate bundle_id",
        lambda data, _b: _second_bundle(data, bundle_id="acme_co_total"),
        "duplicate bundle_id",
    ),
    ("unknown bundle country", _set("country", "XX"), "acme_co_total: unknown country XX"),
    ("bundle id is a service id", _set("bundle_id", "netflix"), "is also a service id"),
    (
        "bundle id is an instrument id",
        _set("bundle_id", "user_co_card"),
        "is also a payment instrument id",
    ),
    ("shared official_url without aliases", _shared_url_without_aliases, "needs aliases"),
    ("shared official_url, same alias", _shared_url_same_alias, "is also acme_co_total's"),
    # curated verdicts
    ("verdict for an unknown profile", _set("verdicts.0.profile", "nope"), "unknown profile nope"),
    ("verdict for a profile at home", _in_sweden, "who lives in SE"),
    ("two verdicts for one profile", _duplicate_verdict, "two verdicts for profile"),
]


@pytest.mark.parametrize(("mutate", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_integrity_rule(mutate, expected: str, cat, profiles, instruments) -> None:
    errors = _errors(_mutated(mutate), cat, profiles, instruments)
    assert any(expected in e for e in errors), errors


def test_every_anchor_fact_that_states_its_value_passes(cat, profiles, instruments) -> None:
    data = _mutated(
        _then(
            _base_plan(),
            _set("price.intro_amount", "9900"),
            _set("valid_until", "2026-12-31"),
            _anchor("base_price", "Acme Base: $ 19.900/mes, sin permanencia"),
            _anchor("intro", "Acme Total: $ 9.900/mes por 3 meses, luego $ 24.900/mes"),
            _anchor("component", "30% OFF en HBO Max y otras plataformas", service_id="hbo_max"),
            _anchor("validity", "Oferta válida hasta el 31 de diciembre de 2026"),
        )
    )
    assert _errors(data, cat, profiles, instruments) == []


def test_two_tiers_with_named_price_anchors_pass(cat, profiles, instruments) -> None:
    assert _errors(_mutated(_esencial_tier), cat, profiles, instruments) == []


def test_validity_anchor_without_a_yaml_date_needs_any_dated_text(
    cat, profiles, instruments
) -> None:
    data = _mutated(_anchor("validity", "Erbjudandet gäller fr.o.m. 1 april 2026"))
    assert _errors(data, cat, profiles, instruments) == []


def test_validity_anchor_states_the_date_in_its_direction(cat, profiles, instruments) -> None:
    """A start date ("fr.o.m.") never confirms an end date of the YAML."""
    data = _mutated(
        _then(
            _set("valid_until", "2026-04-01"),
            _anchor("validity", "Erbjudandet gäller fr.o.m. 1 april 2026, för nya kunder"),
        )
    )
    errors = _errors(data, cat, profiles, instruments)
    assert any("validity anchor does not state 2026-04-01" in e for e in errors), errors
    data = _mutated(
        _then(
            _set("valid_from", "2026-12-31"),
            _anchor("validity", "Oferta válida hasta el 31 de diciembre de 2026"),
        )
    )
    errors = _errors(data, cat, profiles, instruments)
    assert any("validity anchor does not state 2026-12-31" in e for e in errors), errors


def test_a_multi_line_anchor_keeps_its_line_breaks(cat, profiles, instruments) -> None:
    """A duration on the next line may be the next item's: the intro is not confirmed, as it
    would be if the anchor were joined into one line."""
    data = _mutated(
        _then(
            _set("price.intro_amount", "9900"),
            _anchor("intro", "Acme Total: $ 9.900/mes\npor 3 meses"),
        )
    )
    errors = _errors(data, cat, profiles, instruments)
    assert any("intro anchor does not state 9900 COP/month" in e for e in errors), errors
    data = _mutated(
        _then(
            _set("price.intro_amount", "9900"),
            _anchor("intro", "Acme Total:   $ 9.900/mes   por 3 meses"),
        )
    )
    assert _errors(data, cat, profiles, instruments) == []


def test_a_component_anchor_naming_another_tier_fails(cat, profiles, instruments) -> None:
    def other_tiers_line(data: dict, b: dict) -> None:
        _esencial_tier(data, b)
        data["bundles"][1]["watch"]["anchors"][1]["text"] = (
            "Acme Total: incluye Netflix con anuncios y más beneficios"
        )

    errors = _errors(_mutated(other_tiers_line), cat, profiles, instruments)
    assert any(
        "component anchor does not state netflix.standard_with_ads" in e and "Acme Total" in e
        for e in errors
    ), errors


def test_validity_anchor_may_state_the_start_date(cat, profiles, instruments) -> None:
    data = _mutated(
        _then(
            _set("valid_from", "2026-04-01"),
            _set("valid_until", "2027-03-31"),
            _anchor("validity", "Erbjudandet gäller fr.o.m. 1 april 2026, för nya kunder"),
        )
    )
    assert _errors(data, cat, profiles, instruments) == []


def test_price_anchor_with_the_ordinary_price_alone_passes(cat, profiles, instruments) -> None:
    data = _mutated(_set("watch.anchors.0.text", "Acme Total: Precio regular $ 24.900/mes"))
    assert _errors(data, cat, profiles, instruments) == []


def test_anchor_may_quote_the_index_page(cat, profiles, instruments) -> None:
    data = _mutated(_set("watch.anchors.1.url", INDEX))
    assert _errors(data, cat, profiles, instruments) == []


@pytest.mark.parametrize(
    "url",
    ["https://www.acme.com/co/planes", "https://www.acme.com/co", "https://cdn.acme.com.co/x.pdf"],
)
def test_path_prefixed_domains_accept_the_country_path(url, cat, profiles, instruments) -> None:
    data = _mutated(_then(_on_shared_host, _set("official_url", url)))
    assert _errors(data, cat, profiles, instruments) == []


def test_observed_price_needs_no_price_anchor(cat, profiles, instruments) -> None:
    def observed(_data: dict, b: dict) -> None:
        b["price"] = _observed_price("individual")
        b["watch"]["anchors"] = b["watch"]["anchors"][1:]

    assert _errors(_mutated(observed), cat, profiles, instruments) == []


def test_user_report_with_a_date_is_valid(cat, profiles, instruments) -> None:
    def reported(_data: dict, b: dict) -> None:
        b["verified_by"] = "user_report"
        b["user_report"] = {"date": "2026-10-08", "note": "precio visto en la app"}

    assert _errors(_mutated(reported), cat, profiles, instruments) == []


# ---------------------------------------------------- typed state is rejected


@pytest.mark.parametrize(
    "mutate",
    [
        _set("status", "verified"),
        _set("evidence", [{"url": PAGE, "kind": "official", "verified": True}]),
        _set("evidence", [{"url": PAGE, "kind": "official", "retrieved_at": "2026-10-08"}]),
        _set("requirements", {"residency": "yes"}),
        _set("price.requires_base_plan_id", "telia_obegransad_plus"),
        _set("requirements", {"signup_presence": "required"}),
        _set("price.promo_valid_until", "2026-12-31"),
        _set("watch.mode", False),  # what a bare `mode: off` reads as in YAML
        _set("verdicts.0.rank", 2),
    ],
    ids=[
        "status verified",
        "evidence verified",
        "evidence retrieved_at",
        "residency enum",
        "requires_base_plan_id removed",
        "signup_presence enum",
        "promo_valid_until renamed",
        "unquoted off",
        "typed rank",
    ],
)
def test_model_rejects(mutate) -> None:
    with pytest.raises(ValidationError):
        BundlesFile.model_validate(_mutated(mutate))


def test_bare_off_in_yaml_is_rejected() -> None:
    text = yaml.safe_dump(BASE).replace("mode: auto", "mode: off")
    with pytest.raises(ValidationError):
        BundlesFile.model_validate(yaml.safe_load(text))


# -------------------------------------------------------------- migrated files


def test_real_bundles_pass_integrity(cat, profiles, instruments) -> None:
    bundles = load_bundles()
    assert integrity_errors(cat, bundles=bundles, instruments=instruments, profiles=profiles) == []


def test_publish_fixture_passes_integrity() -> None:
    fixture_catalog = load_catalog(PUBLISH_FIXTURES / "catalog")
    bundles = load_bundles(PUBLISH_FIXTURES / "curated")
    assert integrity_errors(fixture_catalog, bundles=bundles) == []


TELIA_PDF = (
    "https://www.telia.se/assets/m/3bb36ae829ef848f/original/"
    "TSP-4667_18_0701_Prislista-Telia-Mobil_B2C.pdf"
)

# The value-bearing fields of every row as they were before the migration (commit 19d05c8):
# (country, amount, currency, period, tax_included, marginal_amount, observed plan,
#  [(service, plan, inclusion, value, ads)], valid_from, valid_until, last_verified,
#  requirements). The migration adds structure only; the v1 feasibility_from summary left the
#  model in milestone H2, replaced by per-profile verdicts.
_STREAMING_ADS = [
    ("netflix", "standard_with_ads", "included", None, True),
    ("disney_plus", "standard_with_ads", "included", None, True),
    ("hbo_max", "basic_with_ads", "included", None, True),
    ("apple_tv", "standard", "included", None, None),
]
_APPLE_ONE = [
    ("apple_tv", "standard", "included", None, None),
    ("apple_arcade", "standard", "included", None, None),
]
PRE_MIGRATION: dict[str, tuple] = {
    "meli_plus_co_total": (
        "CO", "24900", "COP", "month", True, None, None,
        [
            ("disney_plus", "standard_with_ads", "included", None, True),
            ("hbo_max", None, "discount_pct", "30", None),
            ("paramount_plus", None, "discount_pct", "30", None),
            ("vix", None, "discount_pct", "30", None),
        ],
        None, None, "2026-10-08",
        {"local_id": "cedula", "local_line": "unknown", "local_payment": "any_card"},
    ),
    "meli_plus_co_esencial": (
        "CO", "9900", "COP", "month", True, None, None,
        [("hbo_max", None, "discount_pct", "30", None)],
        None, None, "2026-10-08",
        {"local_id": "cedula", "local_payment": "any_card"},
    ),
    "meli_plus_mx_mega": (
        "MX", "399", "MXN", "month", True, None, None, _STREAMING_ADS,
        "2026-02-11", None, "2026-10-08",
        {"local_id": "CURP", "local_payment": "unknown"},
    ),
    "meli_plus_br_mega": (
        "BR", "74.90", "BRL", "month", True, None, None, _STREAMING_ADS,
        "2025-10-07", None, "2026-10-08",
        {"local_id": "CPF", "local_payment": "unknown"},
    ),
    "telia_se_obegransad_streaming": (
        "SE", "699", "SEK", "month", True, "130", None,
        [
            ("netflix", "standard", "included", None, None),
            ("hbo_max", "basic_with_ads", "included", None, True),
            ("disney_plus", "standard_with_ads", "included", None, True),
        ],
        None, None, "2026-10-08",
        {
            "local_id": "personnummer",
            "local_line": "mobile",
            "local_payment": "local_bank",
            "credit_check": True,
        },
    ),
    "go3_lt_filmai_netflix": (
        "LT", "17.99", "EUR", "month", True, None, None,
        [("netflix", None, "included", None, None)],
        "2026-09-04", None, "2026-10-08",
        {"local_line": "none", "local_payment": "unknown"},
    ),
    "apple_one_individual_se": (
        "SE", None, "SEK", "month", True, None, "individual",
        [
            ("apple_music", "individual", "included", None, None),
            *_APPLE_ONE,
            ("icloud_plus", "50gb", "included", None, None),
        ],
        None, None, "2026-10-08",
        {"local_payment": "any_card"},
    ),
    "apple_one_family_se": (
        "SE", None, "SEK", "month", True, None, "family",
        [
            ("apple_music", "family", "included", None, None),
            *_APPLE_ONE,
            ("icloud_plus", "200gb", "included", None, None),
        ],
        None, None, "2026-10-08",
        {"local_payment": "any_card"},
    ),
}  # fmt: skip


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def test_migration_keeps_every_value() -> None:
    rows = {b.bundle_id: b for b in load_bundles().bundles}
    # Rows added later (H10 wave 1) are not part of the migration.
    assert set(PRE_MIGRATION) <= set(rows)
    for bundle_id, expected in PRE_MIGRATION.items():
        b, p = rows[bundle_id], rows[bundle_id].price
        *head, requirements = expected
        assert [
            b.country,
            _str(p.amount),
            p.currency,
            p.period,
            p.tax_included,
            _str(p.marginal_amount),
            p.observed_plan_id,
            [(c.service_id, c.plan_id, c.inclusion, _str(c.value), c.ads) for c in b.components],
            _str(b.valid_from),
            _str(b.valid_until),
            _str(b.last_verified),
        ] == head, bundle_id
        assert b.requirements == BundleRequirements(**requirements), bundle_id


def test_migration_adds_structure_but_no_values() -> None:
    bundles = load_bundles()
    # The rows the H1 migration touched (rows added later, in H10, are checked elsewhere).
    rows = {b.bundle_id: b for b in bundles.bundles if b.bundle_id in PRE_MIGRATION}
    assert {"mercado_libre", "telia_se", "go3", "apple"} <= {p.id for p in bundles.providers}
    assert all(b.verified_by == "search_summary" for b in rows.values())
    assert all(b.status == "unverified" and b.user_report is None for b in rows.values())
    # Anchors come from the bundles probe's output (milestone H10), never typed before.
    assert all(not b.watch.anchors for b in rows.values())
    assert {i for i, b in rows.items() if b.watch.mode == "manual"} == WAVE_1
    assert {i for i, b in rows.items() if b.watch.mode == "off"} == set(rows) - WAVE_1
    for bundle_id in WAVE_1:
        assert str(rows[bundle_id].watch.pages[0].url) == str(rows[bundle_id].official_url)
    telia = rows["telia_se_obegransad_streaming"]
    # H0 probe (2026-10-09): the old bundle page and the edition-18 PDF give 404. The page is
    # the redirect target; the current PDF is found on the price-list index.
    assert str(telia.official_url) == (
        "https://www.telia.se/mobilabonnemang/mobil-obegraensad-plus-netflix-max-disney"
    )
    html_page, pdf_page = telia.watch.pages
    assert (html_page.kind, pdf_page.kind) == ("html", "pdf")
    assert str(pdf_page.url) == str(telia.evidence[0].url) == TELIA_PDF
    assert str(pdf_page.index_url) == "https://www.telia.se/om/priser"
    match = re.search(pdf_page.link_pattern, TELIA_PDF)
    assert match is not None and match["edition"] == "18"
    price = telia.price
    assert (price.base_plan_name, price.amount, price.base_plan_amount, price.marginal_amount) == (
        "Telia Obegränsad Plus",
        699,
        569,
        130,
    )
    go3 = bundles.provider("go3")
    assert "static3.go3.tv" in go3.domains["LT"]
    # One host for every Apple country: the domains carry the country's path.
    assert bundles.provider("apple").domains["SE"] == ["www.apple.com/se/"]


def test_apple_pages_of_another_country_fail(cat, profiles, instruments) -> None:
    """apple.com serves every country: a /co/ or /us/ page never confirms a Swedish bundle."""
    data = load_bundles().model_dump(mode="json")
    (apple,) = [b for b in data["bundles"] if b["bundle_id"] == "apple_one_individual_se"]
    apple["official_url"] = "https://www.apple.com/co/apple-one/"
    apple["watch"]["pages"][0]["url"] = "https://www.apple.com/us/apple-one/"
    errors = _errors(data, cat, profiles, instruments)
    assert len(errors) == 2 and all("is not https on a SE domain" in e for e in errors), errors


def test_meli_co_curated_verdicts_rank_2() -> None:
    """User decision 2: Meli+ CO Total and Esencial for se_resident_co_citizen."""
    rows = {b.bundle_id: b for b in load_bundles().bundles}
    for bundle_id in ("meli_plus_co_total", "meli_plus_co_esencial"):
        (v,) = rows[bundle_id].verdicts
        assert (v.profile, v.legality, v.tos, v.practical) == (
            "se_resident_co_citizen",
            "lawful",
            "breach_unenforced",
            "remote",
        )
        assert (v.status, v.confidence) == ("unverified", "low")
        assert v.notes_es == "según uso propio; sin cita de los términos"
        assert axes_rank(v.legality, v.tos, v.practical, v.steps) == 2
    others = [
        b for i, b in rows.items() if i not in {"meli_plus_co_total", "meli_plus_co_esencial"}
    ]
    assert all(not b.verdicts for b in others)


def test_covered_services_default_to_included_components() -> None:
    rows = {b.bundle_id: b for b in load_bundles().bundles}
    assert rows["meli_plus_co_total"].covered_services() == ["disney_plus"]
    assert rows["meli_plus_co_esencial"].covered_services() == []


def test_service_aliases() -> None:
    cat = load_catalog()
    assert cat.service("hbo_max").aliases == ["HBO Max", "Max"]
    assert "ViX" in cat.service("vix").aliases


# ------------------------------------------------------------------- axes_rank


# The ranks record_rank gave before axes_rank was extracted (commit 19d05c8), as literals: the
# guard is these values, not a comparison of axes_rank with itself.
PRE_H1_RANKS = {
    None: {
        "netflix_co_from_se": 2,
        "youtube_premium_co_from_se": 3,
        "youtube_premium_us_from_se": 4,
        "spotify_co_from_se": 3,
        "disney_plus_co_from_se": 2,
        "hbo_max_co_from_se": 2,
        "icloud_plus_co_from_se": 2,
        "icloud_plus_lt_from_se": 2,
        "google_one_lt_from_se": 2,
        "apple_one_co_from_se": 2,
        "apple_music_co_from_se": 2,
        "apple_tv_co_from_se": 2,
        "microsoft_365_co_from_se": 2,
    },
    PUBLISH_FIXTURES / "curated": {
        "netflix_co_from_se": 2,
        "netflix_us_from_se": 4,
        "spotify_co_from_se": 3,
        "icloud_lt_from_se": 1,
        "apple_one_co_from_se": 2,
        "netflix_co_from_lt": 0,
    },
}


@pytest.mark.parametrize("curated_dir", list(PRE_H1_RANKS), ids=["real", "fixture"])
def test_record_rank_is_unchanged(curated_dir) -> None:
    records = load_feasibility(curated_dir).records
    assert {r.id: record_rank(r) for r in records} == PRE_H1_RANKS[curated_dir]


FALSE_DECLARATION = FeasibilityStep(
    type="billing_address", value="CO", requires_false_declaration=True
)


@pytest.mark.parametrize(
    ("axes", "steps", "expected"),
    [
        (("lawful", "compliant", "remote"), [], 0),
        (("lawful", "compliant", "remote"), [FALSE_DECLARATION], 2),
        (("contestable_eu_right", "compliant", "remote"), [FALSE_DECLARATION], 2),
        (("lawful", "breach_enforced", "remote"), [FALSE_DECLARATION], 3),
        (("contestable_eu_right", "compliant", "presence_once"), [], 1),
        (("lawful", "compliant", "impossible"), [], 4),
    ],
)
def test_axes_rank(axes, steps, expected) -> None:
    assert axes_rank(*axes, steps) == expected
