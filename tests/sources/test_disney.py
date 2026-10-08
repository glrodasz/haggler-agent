import re
from decimal import Decimal
from pathlib import Path

import pytest

from haggler.fetch import RawResponse
from haggler.sources.disney import DisneyWebAdapter

FIX = Path(__file__).parent / "fixtures" / "disney_web"
ADAPTER = DisneyWebAdapter()


def _tasks(catalog):
    svc = catalog.service("disney_plus")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    return {t.countries[0]: t for t in ADAPTER.plan(svc, src, catalog)}


def _parse(catalog, cc, html, *, final_url=None, status=200):
    task = _tasks(catalog)[cc]
    resp = RawResponse(url=task.url, final_url=final_url or task.url, status_code=status, text=html)
    return ADAPTER.parse(task, resp, catalog)


def _prices(result):
    return {o.plan_id: (o.amount, o.currency) for o in result.observations}


def _fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


def _no_json(html):
    return re.sub(r'<script id="__NEXT_DATA__".*?</script>', "", html, flags=re.S)


def _cards(*cards: tuple[str, str]) -> str:
    body = "".join(
        f'<article class="plan-card"><h3>{name}</h3><p class="price">{price}</p>'
        "<ul><li>Full HD</li></ul></article>"
        for name, price in cards
    )
    return f"<html><body><main><section class='plans'>{body}</section></main></body></html>"


CO = {
    "standard_with_ads": (Decimal("24900"), "COP"),
    "standard": (Decimal("33900"), "COP"),
    "premium": (Decimal("44900"), "COP"),
}


def test_plan_requires_disney_locale_and_honours_excludes(catalog):
    tasks = _tasks(catalog)
    assert tasks["SE"].url == "https://www.disneyplus.com/sv-se"
    assert tasks["CO"].url == "https://www.disneyplus.com/es-co"
    assert tasks["EG"].params["not_configured"] == ["disney_locale"]
    assert tasks["EG"].url == ""
    assert not {"IN", "ID", "PK", "NG", "VN"} & set(tasks)


def test_sweden_cards_skip_promo_and_annual(catalog):
    result = _parse(catalog, "SE", _fixture("se.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        "standard_with_ads": (Decimal("69"), "SEK"),  # not the "29 kr/mån i 3 månader" promo
        "standard": (Decimal("109"), "SEK"),  # not "1 090 kr/år"
        "premium": (Decimal("139"), "SEK"),
    }
    swa = next(o for o in result.observations if o.plan_id == "standard_with_ads")
    assert swa.plan_label_raw == "Disney+ Standard med reklam"
    assert "69 kr" in swa.evidence_snippet


def test_colombia_json(catalog):
    result = _parse(catalog, "CO", _fixture("co.html"))
    assert result.status == "ok"
    assert _prices(result) == CO  # Combo+ Premium (a bundle) is ignored
    assert result.notes[0] == "strategy=json"
    assert "$24.900/mes" in result.observations[0].evidence_snippet


def test_colombia_table_without_json(catalog):
    result = _parse(catalog, "CO", _no_json(_fixture("co.html")))
    assert result.notes[0] == "strategy=table"
    assert _prices(result) == CO


def test_colombia_div_grid(catalog):
    html = _no_json(_fixture("co.html"))
    for tag in ("table", "thead", "tbody", "tr", "td", "th"):
        html = re.sub(rf"<(/?){tag}\b", r"<\1div", html)
    result = _parse(catalog, "CO", html)
    assert result.notes[0] == "strategy=columns"
    assert _prices(result) == CO


def test_not_available_text(catalog):
    result = _parse(catalog, "SE", _fixture("not_available.html"))
    assert result.status == "not_available"
    assert result.observations == []


@pytest.mark.parametrize(
    ("final_url", "status_code", "status"),
    [
        ("https://www.disneyplus.com/en-us", 200, "wrong_country"),
        ("https://www.disneyplus.com/en-gb/home", 200, "wrong_country"),
        ("https://www.disneyplus.com/unavailable", 200, "not_available"),
        ("https://www.disneyplus.com/sv-se", 404, "not_available"),
        ("https://www.disneyplus.com/sv-se/", 200, "ok"),
        ("https://www.disneyplus.com/SV-SE?cid=x", 200, "ok"),
    ],
)
def test_final_url_and_status(catalog, final_url, status_code, status):
    html = _fixture("se.html")
    result = _parse(catalog, "SE", html, final_url=final_url, status=status_code)
    assert result.status == status


def test_localized_ads_labels(catalog):
    html = _cards(
        ("Disney+ Standard med reklamer", "59 kr./md."),
        ("Disney+ Standard", "99 kr./md."),
        ("Disney+ Premium", "139 kr./md."),
    )
    assert _prices(_parse(catalog, "DK", html)) == {
        "standard_with_ads": (Decimal("59"), "DKK"),
        "standard": (Decimal("99"), "DKK"),
        "premium": (Decimal("139"), "DKK"),
    }


def test_ads_word_in_unknown_language_moves_to_ads_plan(catalog):
    html = _cards(("Disney+ Standard + reklamy", "29,99 zł/mies."), ("Disney+ Premium", "49,99 zł"))
    assert _prices(_parse(catalog, "PL", html)) == {
        "standard_with_ads": (Decimal("29.99"), "PLN"),
        "premium": (Decimal("49.99"), "PLN"),
    }
    no_ads = _cards(("Disney+ Standard (bez reklam)", "37,99 zł/mies."))
    assert _prices(_parse(catalog, "PL", no_ads)) == {"standard": (Decimal("37.99"), "PLN")}


def test_us_bundles_are_ignored(catalog):
    html = _cards(
        ("Disney+, Hulu Bundle Basic", "$12.99/month"),
        ("Disney+ Basic", "$11.99/month"),
        ("Disney+ Premium", "$18.99/month"),
        ("Disney+, Hulu, Max Bundle Premium", "$29.99/month"),
    )
    result = _parse(catalog, "US", html)
    assert _prices(result) == {
        "standard_with_ads": (Decimal("11.99"), "USD"),
        "premium": (Decimal("18.99"), "USD"),
    }
    assert result.status == "partial"  # the US has no plain "Standard" tier


def test_prices_in_another_currency(catalog):
    html = _cards(("Disney+ Standard", "$9.99/month"), ("Disney+ Premium", "$15.99/month"))
    result = _parse(catalog, "SE", html)
    assert result.status == "wrong_country"


def test_no_prices(catalog):
    html = "<html><body><main><h1>Disney+</h1><p>Logga in</p></main></body></html>"
    assert _parse(catalog, "SE", html).status == "no_prices"
