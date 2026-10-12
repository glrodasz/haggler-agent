"""The bundle research kind (phase 3, H8): its vocabulary, the value hook of the verification
(research.evidence rule 5) and what the agent's job is built from (haggler.bundles.research)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from conftest import FixtureFetcher, frozen_bundles
from haggler.bundles import research as bundle_research
from haggler.catalog import load_catalog, on_domains
from haggler.fetch import RawResponse
from haggler.models.curated import BundlesFile
from haggler.models.fx import FxRate, FxTable
from haggler.models.research import (
    Claim,
    ClaimDraft,
    ResearchAnswer,
    SourceCheck,
    SubjectFacts,
    claim_hash,
    claim_id,
)
from haggler.research import fields as F
from haggler.research.evaluate import POSITIVE_VALUES
from haggler.research.evidence import (
    PageCache,
    ValueContext,
    on_official,
    recheck_claims,
    source_class,
    verify_subject,
)
from haggler.store import dumps_lines
from pdf_support import build_pdf

TODAY = date(2026, 10, 9)
# The frozen bundles.yaml (tests/conftest.py): Meli+ Total at 24900, its three 30 % discounts.
BUNDLES = frozen_bundles()
CATALOG = load_catalog()
TOTAL = next(b for b in BUNDLES.bundles if b.bundle_id == "meli_plus_co_total")
KEY = "bundle:meli_plus_co_total"
PLANES = "https://www.mercadolibre.com.co/suscripciones/melimas/planes"
PRICE_LIST = "https://www.mercadolibre.com.co/l/meli-plus-precios.pdf"
PRESS = "https://www.enter.co/empresas/meli-plus-precios"
CO_DOMAINS = ["mercadolibre.com.co"]
# Lines of the bundles probe on the official Meli+ CO plans page (H0, 2026-10-09).
PRICE_QUOTE = (
    "Precio actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. "
    "Precio anterior: $ 21.900 pesos por mes."
)
DISNEY = "Disney+ Estándar con anuncios."
DISCOUNTS = "30% OFF en HBO Max, Universal+, Paramount+ y ViX Premium."
TOO_CHEAP = "Meli+ Total cuesta $ 1.000 por mes."
DOUBLED = "Meli+ Total cuesta $ 49.900 por mes."
CLAUSE = "Meli+ está disponible únicamente para residentes en Colombia."
PLANES_HTML = f"""<html><body><main>
<section><h2>Meli+ Total</h2>
<p>$ 19.900/mes por 2 meses</p>
<p>{PRICE_QUOTE}</p>
<ul><li>{DISNEY}</li><li>{DISCOUNTS}</li></ul>
<p>{CLAUSE}</p>
</section>
<section><h2>Meli+ Esencial</h2><p>$ 9.900/mes</p></section>
<section><p>{TOO_CHEAP}</p><p>{DOUBLED}</p></section>
</main></body></html>"""
LIST_LINE = "Meli+ Total: $ 21.900 por mes, IVA incluido."
PDF = build_pdf([["Lista de precios Meli+ Colombia", LIST_LINE]])
FX = FxTable(
    as_of=TODAY,
    rates={
        c: FxRate(currency=c, per_eur=Decimal(v), source="ecb", as_of=TODAY)
        for c, v in {"COP": "4700", "SEK": "11.20"}.items()
    },
)


def pdf_response(url: str, body: bytes) -> RawResponse:
    return RawResponse(
        url=url,
        final_url=url,
        status_code=200,
        text=body.decode("utf-8", "replace"),
        content_type="application/pdf",
        body=body,
    )


ROUTES: dict[str, Any] = {
    PLANES: PLANES_HTML,
    PRICE_LIST: pdf_response(PRICE_LIST, PDF),
    PRESS: f"<html><body><p>{DOUBLED}</p><p>{CLAUSE}</p></body></html>",
}


def draft(field: str, value: str, quote: str, url: str = PLANES, event_date: str = "") -> Any:
    return ClaimDraft(field=field, value=value, event_date=event_date, quote=quote, url=url)


def context(fx: FxTable | None = FX) -> ValueContext:
    return bundle_research.value_context(BUNDLES, TOTAL, CATALOG, fx)


async def verify(
    drafts: list[Any],
    routes: dict[str, Any] | None = None,
    *,
    values: ValueContext | str | None = "default",
    known_pages: list[str] | None = None,
) -> Any:
    pages = PageCache(FixtureFetcher(ROUTES if routes is None else routes))
    return await verify_subject(
        drafts,
        key=KEY,
        kind="bundle",
        official_domains=CO_DOMAINS,
        grounding=[],
        pages=pages,
        today=TODAY,
        known_pages=known_pages,
        value_context=context() if values == "default" else values,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------- vocabulary


def test_bundle_vocabulary_values_and_twins():
    names = F.FIELDS_BY_LEVEL["bundle"]
    values = [n for n in names if F.FIELD_SPECS[n].yaml_value]
    assert values == [
        "bundle_price",
        "bundle_intro_price",
        "bundle_base_price",
        "bundle_intro_months",
        "bundle_contract_months",
        "bundle_intro_until",
        "bundle_offer_until",
        "bundle_includes",
        "bundle_discount",
        "bundle_available",
        "bundle_price_list_url",
    ]
    for n in values:
        assert F.FIELD_SPECS[n].official_only, n
    checks = {n: F.FIELD_SPECS[n].value_check for n in values}
    assert checks["bundle_price"] == "money"
    assert checks["bundle_intro_price"] == "intro_money"
    assert checks["bundle_base_price"] == "base_money"
    assert checks["bundle_intro_months"] == checks["bundle_contract_months"] == "months"
    assert checks["bundle_intro_until"] == checks["bundle_offer_until"] == "date_until"
    assert checks["bundle_includes"] == "component"
    assert checks["bundle_discount"] == "discount_pct"  # a component whose number is a percent
    assert checks["bundle_price_list_url"] == "url"
    assert checks["bundle_available"] is None  # the literal quote only
    twins = {
        "bundle_residence_clause": "residence_clause",
        "bundle_foreign_offer_allowed": "foreign_offer_allowed",
        "bundle_enforcement_event": "enforcement_event",
        "bundle_account_country_basis": "account_country_basis",
        "bundle_signup_location": "signup_location",
        "bundle_billing_address": "billing_address",
        "bundle_local_id_required": "local_id_required",
        "bundle_travel_use": "travel_use",
    }
    for twin, original in twins.items():
        a, b = F.FIELD_SPECS[twin], F.FIELD_SPECS[original]
        assert a.level == "bundle" and b.level != "bundle"
        assert (a.value_type, a.values, a.value_labels_es, a.multi, a.needs_date) == (
            b.value_type,
            b.values,
            b.value_labels_es,
            b.multi,
            b.needs_date,
        ), twin
        assert not a.yaml_value and not a.official_only
        assert POSITIVE_VALUES.get(twin) == POSITIVE_VALUES.get(original), twin
    # The bundle-only requirement fields take the values of the curated BundleRequirements.
    assert F.FIELD_SPECS["bundle_local_line"].values == ("none", "mobile", "fixed", "both")
    assert F.FIELD_SPECS["bundle_payment_method"].values == (
        "any_card",
        "local_card",
        "local_bank",
        "carrier_bill",
    )
    assert F.FIELD_SPECS["bundle_credit_check"].values == ("false", "true")
    feasibility = [n for n in names if not F.FIELD_SPECS[n].yaml_value]
    assert set(feasibility) == {*twins, "bundle_local_line", "bundle_payment_method"} | {
        "bundle_credit_check"
    }
    # No Phase 2 field changed class.
    phase2 = [f for f in F.FIELDS if f.level != "bundle"]
    assert not any(f.official_only or f.yaml_value or f.value_check for f in phase2)


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("bundle_price", "21900 COP/month", "21900 COP/month"),
        ("bundle_price", " 21900.00 COP / month ", "21900 COP/month"),
        ("bundle_price", "74.90 BRL/month", "74.90 BRL/month"),
        ("bundle_price", "21900 COP", None),  # the period is part of the value
        ("bundle_price", "21.900 COP/mes", None),
        ("bundle_price", "21,900 COP/month", None),
        ("bundle_price", "$ 21.900", None),
        ("bundle_intro_months", "2", "2"),
        ("bundle_intro_months", "0", None),
        ("bundle_intro_months", "37", None),
        ("bundle_contract_months", "12", "12"),
        ("bundle_contract_months", "0", None),  # "sin permanencia" confirms no number
        ("bundle_intro_until", "2026-12-31", "2026-12-31"),
        ("bundle_intro_until", "2026-02-30", None),
        ("bundle_intro_until", "31/12/2026", None),
        ("bundle_includes", "Disney_Plus.Standard_With_Ads", "disney_plus.standard_with_ads"),
        ("bundle_includes", "disney_plus", "disney_plus"),
        ("bundle_includes", "Disney+", None),
        ("bundle_discount", "hbo_max:30", "hbo_max:30"),
        ("bundle_discount", "hbo_max: 30%", "hbo_max:30"),
        ("bundle_discount", "hbo_max:12.50", "hbo_max:12.5"),
        ("bundle_discount", "hbo_max:0", None),
        ("bundle_discount", "hbo_max:120", None),
        ("bundle_discount", "hbo_max", None),
        ("bundle_available", "FALSE", "false"),
        ("bundle_price_list_url", "https://WWW.Telia.se/a.pdf#p2", "https://www.telia.se/a.pdf"),
        ("bundle_price_list_url", "http://www.telia.se/a.pdf", None),
        ("bundle_price_list_url", "https://localhost/a.pdf", None),
        ("bundle_residence_clause", "must_reside", "must_reside"),
        ("bundle_local_line", "Mobile", "mobile"),
    ],
)
def test_normalize_bundle_values(field: str, raw: str, expected: str | None):
    assert F.normalize_value(F.FIELD_SPECS[field], raw) == expected


def test_pct_type():
    spec = F.FieldSpec("x", "bundle", "pct", "x")
    assert F.normalize_value(spec, "30%") == "30"
    assert F.normalize_value(spec, "101") is None


def test_vocabulary_prompt_says_how_to_write_bundle_values():
    prompt = F.vocabulary_prompt("bundle")
    assert 'bundle_price: an amount like "21900 COP/month"' in prompt
    assert "bundle_intro_months: an integer 1-36" in prompt
    assert "official pages of the job's country only" in prompt
    assert "bundle_enforcement_event:" in prompt and "event_date required" in prompt


def test_bundle_instructions_keep_the_common_rules():
    from haggler.research.agents import COMMON_RULES, instruction_text

    text = instruction_text("bundle")
    assert COMMON_RULES in text
    assert "Never describe or suggest VPNs, proxies, fake addresses, false declarations" in text
    assert "COUNTRY ONLY" in text and "yaml_values" in text
    # The bundle vocabulary only: its twins, never the service and pair fields themselves.
    assert "- bundle_price:" in text and "- bundle_residence_clause:" in text
    assert "- residence_clause:" not in text and "- payment_card_country:" not in text
    assert "suggestions: [] unless the job asks for it" in COMMON_RULES
    assert "suggested_instruments" not in COMMON_RULES


def test_research_answer_still_reads_the_old_suggestions_key():
    old = {
        "status": "nothing_found",
        "claims": [],
        "open_questions": [],
        "suggested_instruments": ["Wise"],
        "notes": "",
    }
    assert ResearchAnswer.model_validate(old).suggestions == ["Wise"]
    new = {**old, "suggestions": ["Revolut"]}
    new.pop("suggested_instruments")
    assert ResearchAnswer.model_validate(new).suggestions == ["Revolut"]
    assert "suggestions" in ResearchAnswer.model_json_schema()["properties"]
    assert "suggested_instruments" not in ResearchAnswer.model_json_schema()["properties"]


def _stored(key: str, field: str, value: str, quote: str, url: str = PLANES) -> Claim:
    return Claim(
        id=claim_id(key, field, value),
        hash=claim_hash(field, value, None, quote, url),
        field=field,
        value=value,
        quote=quote,
        check=SourceCheck(
            cited_url=url,
            final_url=url,
            source_class="official",
            status="verified",
            retrieved_at=datetime(2026, 10, 1, tzinfo=UTC),
        ),
        first_seen=date(2026, 10, 1),
    )


def test_new_fields_stay_out_of_the_json_while_unset():
    claim = _stored("service:netflix", "residence_clause", "must_reside", CLAUSE)
    facts = SubjectFacts(
        key="service:netflix",
        kind="service",
        researched_on=TODAY,
        run_id="r1",
        status="researched",
        claims=[claim],
    )
    row = json.loads(dumps_lines([facts]))[0]
    assert "rechecked_on" not in row and "big_change" not in row["claims"][0]
    flagged = facts.model_copy(
        update={"claims": [claim.model_copy(update={"big_change": True})], "rechecked_on": TODAY}
    )
    row = json.loads(dumps_lines([flagged]))[0]
    assert row["rechecked_on"] == "2026-10-09" and row["claims"][0]["big_change"] is True
    assert SubjectFacts.model_validate(row) == flagged


# ------------------------------------------------------- official pages per country


@pytest.mark.parametrize(
    ("url", "official"),
    [
        ("https://www.apple.com/se/apple-one/", True),
        ("https://www.apple.com/se", True),
        ("https://www.apple.com/co/apple-one/", False),
        ("https://www.apple.com/", False),
        ("https://support.apple.com/se/apple-one/", False),
        # Dot segments are resolved first: these are /co/ pages (httpx fetches /co/apple-one/).
        ("https://www.apple.com/se/../co/apple-one/", False),
        ("https://www.apple.com/se/%2e%2e/co/apple-one/", False),
        ("https://www.apple.com/co/../se/apple-one/", True),
    ],
)
def test_official_domains_with_a_path_prefix(url: str, official: bool):
    domains = ["www.apple.com/se/"]
    assert on_official(url, domains) is official
    assert (source_class(url, domains) == "official") is official
    assert on_domains(url, domains) is official


def test_plain_domains_classify_as_before():
    assert source_class("https://help.netflix.com/legal/termsofuse", ["netflix.com"]) == "official"
    assert source_class("https://www.techradar.com/x", ["netflix.com"]) == "press"


# --------------------------------------------------------------------- value hook


async def test_value_claims_verify_when_the_quote_states_the_value():
    out = await verify(
        [
            draft("bundle_price", "21900 COP/month", PRICE_QUOTE),
            draft("bundle_intro_price", "19900 COP/month", PRICE_QUOTE),
            draft("bundle_intro_months", "2", PRICE_QUOTE),
            draft("bundle_includes", "disney_plus.standard_with_ads", DISNEY),
            draft("bundle_discount", "hbo_max:30", DISCOUNTS),
        ]
    )
    assert [u.status for u in out.unverified] == []
    assert sorted(c.field for c in out.claims) == [
        "bundle_discount",
        "bundle_includes",
        "bundle_intro_months",
        "bundle_intro_price",
        "bundle_price",
    ]
    price = next(c for c in out.claims if c.field == "bundle_price")
    assert price.id == f"{KEY}:bundle_price:21900 COP/month" and not price.big_change
    assert price.check.source_class == "official"


@pytest.mark.parametrize(
    ("field", "value", "quote", "status", "detail"),
    [
        ("bundle_price", "24900 COP/month", PRICE_QUOTE, "value_not_in_quote", "24900"),
        ("bundle_price", "19900 COP/month", PRICE_QUOTE, "value_not_in_quote", "intro"),
        ("bundle_intro_months", "3", PRICE_QUOTE, "value_not_in_quote", "months"),
        ("bundle_includes", "hbo_max", DISCOUNTS, "value_not_in_quote", "discount"),
        ("bundle_discount", "hbo_max:20", DISCOUNTS, "value_not_in_quote", "20"),
        ("bundle_price", "1000 COP/month", TOO_CHEAP, "implausible", "0.5-300"),
    ],
)
async def test_value_claims_the_quote_does_not_state_are_not_persisted(
    field: str, value: str, quote: str, status: str, detail: str
):
    out = await verify([draft(field, value, quote)])
    assert out.claims == []
    (u,) = out.unverified
    assert u.status == status and detail in u.detail
    assert out.checks == [("official", status)]


# Numbers a page gives a service that are no percentage discount.
NOT_DISCOUNTS = (
    "Con Meli+ Total tienes 2 meses gratis de HBO Max.",
    "Incluye HBO Max para 2 dispositivos a la vez.",
    "Ahorra US$ 5 en HBO Max cada mes con Meli+ Total.",
)


@pytest.mark.parametrize(
    ("value", "quote"),
    [
        ("hbo_max:2", NOT_DISCOUNTS[0]),
        ("hbo_max:2", NOT_DISCOUNTS[1]),
        ("hbo_max:5", NOT_DISCOUNTS[2]),
    ],
)
async def test_a_discount_needs_a_percentage_not_free_months_devices_or_money(
    value: str, quote: str
):
    html = "<html><body>" + "".join(f"<p>{q}</p>" for q in NOT_DISCOUNTS) + "</body></html>"
    out = await verify([draft("bundle_discount", value, quote)], {PLANES: html})
    assert out.claims == []
    (u,) = out.unverified
    assert u.status == "value_not_in_quote" and "percentage" in u.detail
    # The same number written as a percentage of that service's clause does confirm it.
    pct = "Ahorra 5% en HBO Max cada mes con Meli+ Total."
    out = await verify([draft("bundle_discount", "hbo_max:5", pct)], {PLANES: f"<p>{pct}</p>"})
    assert [c.value for c in out.claims] == ["hbo_max:5"]


async def test_a_big_change_against_the_yaml_is_kept_on_the_claim():
    out = await verify([draft("bundle_price", "49900 COP/month", DOUBLED)])
    (claim,) = out.claims
    assert claim.big_change  # 24900 in bundles.yaml, the page now says 49900


async def test_without_fx_plausibility_is_not_checked():
    out = await verify([draft("bundle_price", "1000 COP/month", TOO_CHEAP)], values=context(None))
    assert [c.value for c in out.claims] == ["1000 COP/month"]


async def test_a_value_claim_without_its_bundle_fails_closed():
    out = await verify([draft("bundle_price", "21900 COP/month", PRICE_QUOTE)], values=None)
    assert out.claims == [] and out.unverified[0].status == "value_not_in_quote"


async def test_values_need_an_official_page_of_the_bundles_country():
    press = draft("bundle_price", "49900 COP/month", DOUBLED, url=PRESS)
    out = await verify([press])
    assert out.claims == []
    assert out.unverified[0].status == "invalid" and "official" in out.unverified[0].detail
    assert out.checks == [("press", "invalid")]
    # The same quote on a known official page verifies there.
    out = await verify([press], known_pages=[PLANES])
    assert [c.check.via for c in out.claims] == ["known_page"]
    assert out.claims[0].url == PLANES


async def test_feasibility_twins_take_the_phase2_checks_only():
    out = await verify(
        [
            draft("bundle_residence_clause", "must_reside", CLAUSE),
            draft("bundle_residence_clause", "must_reside", CLAUSE, url=PRESS),
        ],
        values=None,
    )
    (claim,) = out.claims  # one claim per id: official beats press
    assert claim.check.source_class == "official" and not claim.big_change
    out = await verify([draft("bundle_residence_clause", "must_reside", CLAUSE, url=PRESS)])
    assert out.claims[0].check.source_class == "press"


async def test_a_value_in_an_official_pdf_and_the_price_list_url():
    out = await verify(
        [
            draft("bundle_price", "21900 COP/month", LIST_LINE, url=PRICE_LIST),
            draft("bundle_price_list_url", PRICE_LIST, LIST_LINE, url=PRICE_LIST),
            # The quote is not in the document the value names.
            draft("bundle_price_list_url", PRICE_LIST, PRICE_QUOTE, url=PLANES),
        ]
    )
    assert sorted((c.field, c.url) for c in out.claims) == [
        ("bundle_price", PRICE_LIST),
        ("bundle_price_list_url", PRICE_LIST),
    ]
    (u,) = out.unverified
    assert u.status == "value_not_in_quote" and "document" in u.detail


async def test_a_price_list_url_needs_the_quote_from_that_document():
    # The cited PDF does not have the quote; a known page does, but it is not the price list.
    out = await verify(
        [draft("bundle_price_list_url", PRICE_LIST, PRICE_QUOTE, url=PRICE_LIST)],
        known_pages=[PLANES],
    )
    assert out.claims == [] and out.unverified[0].status == "value_not_in_quote"


async def test_a_price_list_of_another_country_is_not_a_value():
    url = "https://www.mercadolibre.com.mx/l/precios.pdf"
    routes = {**ROUTES, url: pdf_response(url, PDF)}
    out = await verify([draft("bundle_price_list_url", url, LIST_LINE, url=url)], routes)
    assert out.claims == [] and out.unverified[0].status == "invalid"


async def test_recheck_drops_a_value_its_quote_no_longer_states():
    stale = _stored(KEY, "bundle_price", "24900 COP/month", PRICE_QUOTE)
    pages = PageCache(FixtureFetcher(ROUTES))
    kept, dropped = await recheck_claims(
        [stale], pages=pages, official_domains=CO_DOMAINS, value_context=context()
    )
    assert kept == [] and [d.status for d in dropped] == ["value_not_in_quote"]
    good = _stored(KEY, "bundle_price", "21900 COP/month", PRICE_QUOTE)
    kept, dropped = await recheck_claims(
        [good], pages=pages, official_domains=CO_DOMAINS, value_context=context()
    )
    assert [c.id for c in kept] == [good.id] and dropped == []
    # Without a value context (a Phase 2 recheck) only the quote counts, as before.
    kept, _ = await recheck_claims([stale], pages=pages, official_domains=CO_DOMAINS)
    assert [c.id for c in kept] == [stale.id]


@pytest.mark.parametrize(
    "wall",
    [
        RawResponse(
            url=PLANES,
            final_url="https://www.mercadolibre.com.co/gz/account-verification?go=x",
            status_code=200,
            text="<html><body><h1>Verifica tu cuenta</h1></body></html>",
        ),
        RawResponse(
            url=PLANES,
            final_url=PLANES,
            status_code=200,
            text="<html><head><title>Just a moment...</title></head><body></body></html>",
        ),
    ],
    ids=["account_verification", "challenge"],
)
async def test_recheck_keeps_a_bundle_finding_behind_a_wall_that_answers_200(wall):
    good = _stored(KEY, "bundle_price", "21900 COP/month", PRICE_QUOTE)
    clause = _stored(KEY, "bundle_residence_clause", "must_reside", CLAUSE)
    pages = PageCache(FixtureFetcher({**ROUTES, PLANES: wall}))
    kept, dropped = await recheck_claims(
        [good, clause], pages=pages, official_domains=CO_DOMAINS, value_context=context()
    )
    # Unreadable today: kept as they were (never "no longer on the page").
    assert kept == [good, clause] and dropped == []


# --------------------------------------------------------------- the agent's job


def test_yaml_values_in_the_vocabulary_forms():
    assert bundle_research.yaml_values(TOTAL) == {
        "bundle_price": "24900 COP/month",
        "bundle_includes": ["disney_plus.standard_with_ads"],
        "bundle_discount": ["hbo_max:30", "paramount_plus:30", "vix:30"],
    }
    for field, value in bundle_research.yaml_values(TOTAL).items():
        spec = F.FIELD_SPECS[field]
        for v in value if isinstance(value, list) else [value]:
            assert F.normalize_value(spec, v) == v


def test_job_known_pages_and_country_domains():
    job = bundle_research.job(BUNDLES, TOTAL, CATALOG)
    assert job["official_domains"] == ["mercadolibre.com.co"]
    assert job["known_pages"] == [PLANES]
    assert job["country"] == "CO" and job["currency"] == "COP"
    assert job["yaml_values"]["bundle_price"] == "24900 COP/month"
    assert job["service_ids"]["disney_plus"] == "Disney+"
    assert [c["service_id"] for c in job["components"]] == [
        "disney_plus",
        "hbo_max",
        "paramount_plus",
        "vix",
    ]
    json.dumps(job)  # the payload is plain JSON
    apple = next(b for b in BUNDLES.bundles if b.bundle_id == "apple_one_individual_se")
    assert bundle_research.official_domains(BUNDLES, apple) == ["www.apple.com/se/"]


def test_known_pages_add_the_newer_edition_the_watcher_found():
    from haggler.models.bundles import BundleWatchState, PageCheck

    row = TOTAL.model_dump(mode="json")
    row["watch"]["pages"].append({"url": PRICE_LIST, "kind": "pdf"})
    data = BundlesFile.model_validate(
        {"providers": BUNDLES.model_dump(mode="json")["providers"], "bundles": [row]}
    )
    (b,) = data.bundles
    newer = "https://www.mercadolibre.com.co/l/meli-plus-precios-2.pdf"
    watch = BundleWatchState(
        bundle_id=b.bundle_id,
        outcome="new_document",
        pages=[PageCheck(url=PRICE_LIST, kind="pdf", status="superseded", newest_url=newer)],
    )
    assert bundle_research.known_pages(b, watch) == [PLANES, PRICE_LIST, newer]
    assert bundle_research.yaml_values(b)["bundle_price_list_url"] == PRICE_LIST
