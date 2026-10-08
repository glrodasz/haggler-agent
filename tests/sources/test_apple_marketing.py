from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from haggler.fetch import RawResponse
from haggler.sources.apple_marketing import AppleMarketingAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "apple_marketing"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def adapter() -> AppleMarketingAdapter:
    return AppleMarketingAdapter()


@pytest.fixture(scope="module")
def run(adapter, catalog):
    """run(service_id, cc, html, final_url=None, status=200) -> ParseResult"""

    def _run(service_id: str, cc: str, html: str, final_url: str | None = None, status=200):
        svc = catalog.service(service_id)
        task = next(t for t in adapter.plan(svc, svc.sources[0], catalog) if t.countries == [cc])
        resp = RawResponse(
            url=task.url, final_url=final_url or task.url, status_code=status, text=html
        )
        return adapter.parse(task, resp, catalog)

    return _run


def _prices(result) -> dict[str, tuple[Decimal, str]]:
    return {o.plan_id: (o.amount, o.currency) for o in result.observations}


def _page(body: str, *, lang: str = "en-US", canonical: str | None = None) -> str:
    link = f'<link rel="canonical" href="{canonical}" />' if canonical else ""
    return (
        f'<!DOCTYPE html><html lang="{lang}"><head><title>Apple</title>{link}</head>'
        f'<body><main id="main" class="main">{body}</main></body></html>'
    )


# ---------------------------------------------------------------- plan


def test_plan_urls_per_storefront(adapter, catalog):
    svc = catalog.service("apple_one")
    tasks = {t.countries[0]: t for t in adapter.plan(svc, svc.sources[0], catalog)}
    assert len(tasks) == len(catalog.countries)
    assert tasks["SE"].url == "https://www.apple.com/se/apple-one/"
    assert tasks["US"].url == "https://www.apple.com/apple-one/"
    assert tasks["GB"].url == "https://www.apple.com/uk/apple-one/"
    assert tasks["CH"].url == "https://www.apple.com/chde/apple-one/"
    assert tasks["SE"].task_id == "apple_marketing:apple_one:SE"
    assert tasks["SE"].host == "www.apple.com"
    assert tasks["SE"].params["page"] == "apple-one"
    assert "not_configured" not in tasks["US"].params


@pytest.mark.parametrize("cc", ["AR", "PE", "PK", "EG", "NG"])
def test_plan_marks_countries_without_apple_path(adapter, catalog, cc):
    svc = catalog.service("apple_music")
    task = next(t for t in adapter.plan(svc, svc.sources[0], catalog) if t.countries == [cc])
    assert task.params["not_configured"] == ["apple_path"]
    assert task.url == "" and task.host == ""


# ---------------------------------------------------------------- Sweden


def test_se_apple_one(run):
    result = run("apple_one", "SE", _html("se_apple_one.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        "individual": (Decimal("195"), "SEK"),
        "family": (Decimal("255"), "SEK"),
    }
    # Premier is not sold in Sweden: reported, but not a problem.
    assert result.missing_plans == ["premier"]
    labels = {o.plan_id: o.plan_label_raw for o in result.observations}
    assert labels == {"individual": "Individuell", "family": "Familj"}


def test_se_apple_music_ignores_apple_one_card(run):
    result = run("apple_music", "SE", _html("se_apple_music.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        "student": (Decimal("65"), "SEK"),
        "individual": (Decimal("129"), "SEK"),
        "family": (Decimal("199"), "SEK"),
    }
    assert result.missing_plans == []


def test_se_apple_tv_skips_intro_offer(run):
    result = run("apple_tv", "SE", _html("se_apple_tv.html"))
    assert result.status == "ok"
    assert _prices(result) == {"standard": (Decimal("99"), "SEK")}
    [obs] = result.observations
    assert obs.billing_period == "month"
    assert "99 kr/mån." in obs.evidence_snippet


# ---------------------------------------------------------------- Colombia


def test_co_apple_one(run):
    result = run("apple_one", "CO", _html("co_apple_one.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        "individual": (Decimal("26900"), "COP"),
        "family": (Decimal("34900"), "COP"),
    }
    assert result.missing_plans == ["premier"]


def test_co_apple_music_spanish_labels(run):
    result = run("apple_music", "CO", _html("co_apple_music.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        "student": (Decimal("8900"), "COP"),
        "individual": (Decimal("16900"), "COP"),
        "family": (Decimal("25900"), "COP"),
    }


def test_co_apple_tv_from_ld_json(run):
    result = run("apple_tv", "CO", _html("co_apple_tv.html"))
    assert result.status == "ok"
    assert _prices(result) == {"standard": (Decimal("14900"), "COP")}
    [obs] = result.observations
    assert obs.evidence_snippet.startswith("ld+json Offer")
    assert any("ld+json" in n for n in result.notes)
    assert not any("vs text" in n for n in result.notes)  # text strategy agrees


def test_co_apple_tv_text_fallback_without_ld_json(run):
    ld_json = re.compile(r'<script type="application/ld\+json">.*?</script>', re.S)
    html = ld_json.sub("", _html("co_apple_tv.html"))
    assert "ld+json" not in html
    result = run("apple_tv", "CO", html)
    assert result.status == "ok"
    # The MLS Season Pass price ($ 59.900) is a different product.
    assert _prices(result) == {"standard": (Decimal("14900"), "COP")}
    assert result.observations[0].evidence_snippet.startswith("$ 14.900/mes")


def test_co_page_priced_in_usd_is_wrong_country(run):
    result = run("apple_music", "CO", _html("co_apple_music_usd.html"))
    assert result.status == "wrong_country"
    assert result.observations == []
    assert any("USD" in n and "COP" in n for n in result.notes)


# ---------------------------------------------------------------- United States


def test_us_apple_one_with_premier_and_savings_lines(run):
    result = run("apple_one", "US", _html("us_apple_one.html"))
    assert result.status == "ok"
    # "Save over $8/mo." sits above the Family price and must not be taken for it.
    assert _prices(result) == {
        "individual": (Decimal("19.95"), "USD"),
        "family": (Decimal("25.95"), "USD"),
        "premier": (Decimal("37.95"), "USD"),
    }
    assert result.missing_plans == []


def test_us_apple_music_apple_one_card_first(run):
    result = run("apple_music", "US", _html("us_apple_music.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        "student": (Decimal("5.99"), "USD"),
        "individual": (Decimal("10.99"), "USD"),
        "family": (Decimal("16.99"), "USD"),
    }


def test_us_apple_tv_regular_price_despite_promos_and_bundles(run):
    result = run("apple_tv", "US", _html("us_apple_tv.html"))
    assert result.status == "ok"
    assert _prices(result) == {"standard": (Decimal("12.99"), "USD")}


@pytest.mark.parametrize(
    ("service_id", "cc", "fixture"),
    [
        ("apple_one", "SE", "se_apple_one.html"),
        ("apple_music", "CO", "co_apple_music.html"),
        ("apple_tv", "US", "us_apple_tv.html"),
        ("apple_tv", "CO", "co_apple_tv.html"),
    ],
)
def test_drafts_are_apple_billed(run, service_id, cc, fixture):
    result = run(service_id, cc, _html(fixture))
    assert result.observations
    for obs in result.observations:
        assert obs.flags == ["apple_billing"]
        assert obs.service_id == service_id
        assert obs.country == cc
        assert obs.evidence_snippet


# ---------------------------------------------------------------- wrong country / availability


def test_unknown_storefront_redirected_to_us_page(run):
    result = run(
        "apple_one", "LT", _html("us_apple_one.html"), final_url="https://www.apple.com/apple-one/"
    )
    assert result.status == "wrong_country"
    assert result.observations == []
    assert "/lt/" in result.notes[0]


def test_us_task_redirected_to_other_storefront(run):
    result = run(
        "apple_one",
        "US",
        _html("se_apple_one.html"),
        final_url="https://www.apple.com/uk/apple-one/",
    )
    assert result.status == "wrong_country"


def test_canonical_of_another_storefront_is_wrong_country(run):
    # Replayed raw page (final URL unknown) whose canonical link is the US page.
    result = run("apple_music", "SE", _html("us_apple_music.html"))
    assert result.status == "wrong_country"
    assert result.notes[0].startswith("canonical")


def test_redirect_off_apple_com(run):
    result = run(
        "apple_music", "SE", _html("se_apple_music.html"), final_url="https://example.com/se/"
    )
    assert result.status == "wrong_country"


def test_redirect_to_storefront_home_is_not_available(run):
    result = run("apple_tv", "SE", _page("<h1>Apple</h1>"), final_url="https://www.apple.com/se/")
    assert result.status == "not_available"


def test_http_404_is_not_available(run):
    assert run("apple_one", "VN", _page("<h1>Not found</h1>"), status=404).status == "not_available"


def test_blocked(run):
    html = (
        "<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>"
        'You don\'t have permission to access "http://www.apple.com/se/apple-one/" on this '
        "server.<p>Reference #18.6b2f1402.1759900000.1a2b3c</p></body></html>"
    )
    assert run("apple_one", "SE", html).status == "blocked"
    assert run("apple_one", "SE", "", status=403).status == "blocked"


def test_lang_mismatch_is_noted(run):
    html = _page("<h3>Individuell</h3><p>195 kr/mån.</p>", lang="nb-NO")
    result = run("apple_one", "SE", html)
    assert result.status == "ok"
    assert any("nb-NO" in n for n in result.notes)


# ---------------------------------------------------------------- statuses on synthetic snippets


def test_partial_when_default_plan_missing(run):
    result = run("apple_one", "US", _page("<h3>Family</h3><p>$25.95/mo.</p>"))
    assert result.status == "partial"
    assert _prices(result) == {"family": (Decimal("25.95"), "USD")}
    assert result.missing_plans == ["individual", "premier"]


def test_no_prices_when_labels_have_no_price(run):
    result = run("apple_music", "SE", _page("<h3>Individuell</h3><p>Prova gratis</p>"))
    assert result.status == "no_prices"
    assert result.observations == []


def test_unparsed_local_prices_are_no_prices_not_wrong_country(run):
    # Plan names in a language the catalog has no aliases for: prices exist, in SEK.
    result = run("apple_music", "SE", _page("<h3>Indywidualny</h3><p>129 kr/mån.</p>"))
    assert result.status == "no_prices"


def test_ld_json_offers_per_plan(run):
    ld = (
        '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",'
        '"name":"Apple One","offers":[{"@type":"Offer","name":"Individual","price":"19.95",'
        '"priceCurrency":"USD","priceSpecification":{"@type":"UnitPriceSpecification",'
        '"price":"19.95","priceCurrency":"USD","referenceQuantity":{"@type":"QuantitativeValue",'
        '"value":1,"unitCode":"MON"}}},{"@type":"Offer","name":"Family","price":25.95,'
        '"priceCurrency":"USD"},{"@type":"Offer","name":"Premier","price":"379.50",'
        '"priceCurrency":"USD","priceSpecification":{"billingDuration":"P1Y"}}]}</script>'
    )
    result = run("apple_one", "US", _page(ld + "<h1>Apple One</h1>"))
    assert result.status == "ok"
    got = {o.plan_id: (o.amount, o.billing_period) for o in result.observations}
    assert got == {
        "individual": (Decimal("19.95"), "month"),
        "family": (Decimal("25.95"), "month"),
        "premier": (Decimal("379.50"), "year"),
    }


def test_ld_json_in_foreign_currency_is_wrong_country(run):
    ld = (
        '<script type="application/ld+json">{"@type":"Product","name":"Apple TV",'
        '"offers":{"@type":"Offer","price":"12.99","priceCurrency":"USD"}}</script>'
    )
    result = run("apple_tv", "SE", _page(ld + "<h1>Apple TV</h1>", lang="sv-SE"))
    assert result.status == "wrong_country"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Free for 1 month, then $10.99/mo.", "10.99"),
        ("$2.99/mo. for 3 months, then $12.99/mo.", "12.99"),
        ("Get 3 months for $2.99/mo. Then $12.99/mo.", "12.99"),
        ("Try it free for 7 days. $12.99/month after trial.", "12.99"),
    ],
)
def test_single_plan_price_roles(run, line, expected):
    result = run("apple_tv", "US", _page(f"<h1>Apple TV</h1><p>{line}</p>"))
    assert _prices(result) == {"standard": (Decimal(expected), "USD")}


def test_single_plan_period_in_next_block(run):
    body = '<h1>Apple TV</h1><p class="price">99 kr</p><p class="period">/mån.</p>'
    html = _page(body, lang="sv-SE")
    result = run("apple_tv", "SE", html)
    assert _prices(result) == {"standard": (Decimal("99"), "SEK")}


def test_single_plan_ignores_hardware_and_installments(run):
    html = _page(
        "<h1>Apple TV</h1><p>Apple TV 4K from $129 or $10.75/mo. for 12 mo.</p>"
        "<p>Pay with Apple Card Monthly Installments: $10.75/mo.</p>"
    )
    result = run("apple_tv", "US", html)
    assert result.status == "no_prices"


def test_inline_markup_and_footnotes_do_not_split_labels(run):
    html = _page(
        '<div class="plan"><h3>Individual<sup class="footnote"><a href="#f1">1</a></sup></h3>'
        '<p><span class="currency">$</span><span class="amount">19.95</span>/mo.</p></div>'
    )
    result = run("apple_one", "US", html)
    assert _prices(result) == {"individual": (Decimal("19.95"), "USD")}


def test_footnote_sup_inside_price_and_label(run):
    html = _page(
        '<h3>Student<sup class="footnote"><a href="#f3">3</a></sup></h3>'
        '<p>$5.99<sup class="footnote"><a href="#f3">3</a></sup>/mo.</p>'
        "<h3>Individual</h3><p>$10.99/mo.</p>"
    )
    result = run("apple_music", "US", html)
    assert _prices(result) == {
        "student": (Decimal("5.99"), "USD"),
        "individual": (Decimal("10.99"), "USD"),
    }
    assert result.observations[0].plan_label_raw == "Student"
