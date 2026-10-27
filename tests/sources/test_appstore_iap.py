import re
from decimal import Decimal
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser

from haggler.fetch import RawResponse
from haggler.models.catalog import SourceDef
from haggler.sources.appstore_iap import (
    FLAG_DUPLICATE,
    FLAG_MARKUP,
    AppStoreIapAdapter,
    collect_items,
    infer_periods,
    is_iap_heading,
    items_from_html,
    items_from_json,
    items_from_text,
    name_period,
    text_lines,
)
from haggler.sources.base import match_iap_plan
from haggler.sources.registry import get_adapter

FIX = Path(__file__).parent / "fixtures" / "appstore_iap"
ADAPTER = AppStoreIapAdapter()


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _task(catalog, service_id, cc):
    svc = catalog.service(service_id)
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    return next(t for t in ADAPTER.plan(svc, src, catalog) if t.countries == [cc])


def _parse(catalog, service_id, cc, html, *, final_url=None, status=200):
    task = _task(catalog, service_id, cc)
    resp = RawResponse(url=task.url, final_url=final_url or task.url, status_code=status, text=html)
    return ADAPTER.parse(task, resp, catalog)


def _prices(result):
    return {(o.plan_id, o.billing_period): (o.amount, o.currency) for o in result.observations}


def _pairs(items):
    return [(i.name, i.price_text) for i in items]


# ---------------------------------------------------------------- planning


def test_registered():
    assert isinstance(get_adapter("appstore_iap"), AppStoreIapAdapter)


def test_plan_one_task_per_storefront(catalog):
    svc = catalog.service("youtube_premium")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    tasks = ADAPTER.plan(svc, src, catalog)
    assert len(tasks) == len(catalog.countries)
    se = next(t for t in tasks if t.countries == ["SE"])
    assert se.url == "https://apps.apple.com/se/app/id544007664"
    assert se.host == "apps.apple.com"
    assert se.channel == "ios_iap"
    assert se.task_id == "appstore_iap:youtube_premium:SE"
    assert se.params["app_id"] == 544007664
    assert se.params["exclude_patterns"] == ["music"]
    assert "not_configured" not in se.params
    # AR has no apple.com path, but the App Store storefront needs nothing beyond the code.
    ar = next(t for t in tasks if t.countries == ["AR"])
    assert ar.url == "https://apps.apple.com/ar/app/id544007664"


def test_plan_honours_source_exclusions(catalog):
    svc = catalog.service("hbo_max")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    ccs = {t.countries[0] for t in ADAPTER.plan(svc, src, catalog)}
    assert "CA" not in ccs and "IN" not in ccs and "SE" in ccs


def test_plan_without_app_id_is_not_configured(catalog):
    svc = catalog.service("google_one")
    src = SourceDef(adapter="appstore_iap", channel="ios_iap", countries=["SE"])
    (task,) = ADAPTER.plan(svc, src, catalog)
    assert task.params["not_configured"] == ["app_id"]
    assert task.url == "" and task.host == ""


# ---------------------------------------------------------------- full pages


def test_us_youtube_2025_design(catalog):
    result = _parse(catalog, "youtube_premium", "US", _html("us_youtube_premium.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        ("individual", "month"): (Decimal("15.99"), "USD"),
        ("family", "month"): (Decimal("34.99"), "USD"),
        ("student", "month"): (Decimal("10.99"), "USD"),
        ("lite", "month"): (Decimal("8.99"), "USD"),
    }
    by_plan = {o.plan_id: o for o in result.observations}
    # "YouTube Premium" is listed at $18.99 and $15.99: not a monthly/annual pair.
    assert FLAG_DUPLICATE in by_plan["individual"].flags
    assert by_plan["family"].flags == [FLAG_MARKUP]
    assert all(FLAG_MARKUP in o.flags for o in result.observations)
    assert by_plan["family"].plan_label_raw == "YouTube Premium (Family)"
    assert "$34.99" in by_plan["family"].evidence_snippet
    # "YouTube Music Premium" is excluded by the source's exclude_patterns; "Super Chat" matches
    # no plan and the source sets drop_unmatched, so neither goes to the LLM.
    assert not result.unmapped and not result.llm_requests
    assert result.missing_plans == ["duo"]
    assert any("excluded 1" in n for n in result.notes)
    assert any("dropped 1 item(s)" in n for n in result.notes)


def test_us_youtube_every_extractor_reads_the_list(catalog):
    html = _html("us_youtube_premium.html")
    tree = LexborHTMLParser(html)
    expected = ["USD"]
    full = [
        ("YouTube Premium", "$18.99"),
        ("YouTube Premium (Family)", "$34.99"),
        ("YouTube Music Premium", "$14.99"),
        ("YouTube Premium Student", "$10.99"),
        ("Premium Lite", "$8.99"),
        ("YouTube Premium", "$15.99"),
        ("Super Chat", "$4.99"),
    ]
    # JSON: Annotation text pairs and items_V3 dedupe; the paid lockup is not an IAP.
    assert _pairs(items_from_json(tree, expected)) == full
    assert _pairs(items_from_html(tree, expected)) == full
    assert _pairs(items_from_text(text_lines(html), expected)) == full


def test_se_youtube_pre_2025_design_with_shoebox(catalog):
    html = _html("se_youtube_premium.html")
    tree = LexborHTMLParser(html)
    # The shoebox only caches the "top" purchases; the list shows two more.
    assert [i.name for i in items_from_json(tree, ["SEK"])] == [
        "YouTube Premium",
        "YouTube Premium – familj",
        "YouTube Music Premium",
    ]
    assert len(items_from_html(tree, ["SEK"])) == 5

    result = _parse(catalog, "youtube_premium", "SE", html)
    assert result.status == "ok"
    assert _prices(result) == {
        ("individual", "month"): (Decimal("149.00"), "SEK"),
        ("individual", "year"): (Decimal("1490.00"), "SEK"),
        ("family", "month"): (Decimal("249.00"), "SEK"),
        ("student", "month"): (Decimal("79.00"), "SEK"),
    }
    # 1 490 kr is 10x 149 kr: an annual price, not a duplicate.
    assert all(o.flags == [FLAG_MARKUP] for o in result.observations)
    assert not result.unmapped and not result.llm_requests
    # Paid apps from "More by this developer" (59 kr) never become purchases.
    assert all(o.amount != Decimal("59") for o in result.observations)


def test_co_google_one_text_only(catalog):
    html = _html("co_google_one.html")
    tree = LexborHTMLParser(html)
    assert items_from_json(tree, ["COP"]) == [] and items_from_html(tree, ["COP"]) == []

    result = _parse(catalog, "google_one", "CO", html)
    assert result.status == "ok"
    assert _prices(result) == {
        ("100gb", "month"): (Decimal("8900"), "COP"),
        ("100gb", "year"): (Decimal("89900"), "COP"),
        ("200gb", "month"): (Decimal("11900"), "COP"),
        ("2tb", "month"): (Decimal("42900"), "COP"),
        ("2tb", "year"): (Decimal("429900"), "COP"),
        ("ai_pro", "month"): (Decimal("96900"), "COP"),
    }
    # "Google AI Ultra" is excluded; plain "Google One" matches no plan pattern.
    assert [(i.name, i.amount) for i in result.unmapped] == [("Google One", Decimal("8900"))]
    assert result.llm_requests[0].page_text == "Google One — $ 8.900"
    assert result.missing_plans == ["ai_plus"]


def test_ar_storefront_bills_in_usd(catalog):
    result = _parse(catalog, "google_one", "AR", _html("ar_google_one.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        ("100gb", "month"): (Decimal("1.99"), "USD"),
        ("200gb", "month"): (Decimal("2.99"), "USD"),
        ("2tb", "month"): (Decimal("9.99"), "USD"),
        ("ai_pro", "month"): (Decimal("19.99"), "USD"),
    }
    # "Cloud Vault Pro US$ 2,99" sits in a similar-apps shelf, not in the IAP annotation.
    assert not result.unmapped


def test_se_hbo_max_html_list_only(catalog):
    html = _html("se_hbo_max.html")
    assert items_from_json(LexborHTMLParser(html), ["SEK"]) == []
    result = _parse(catalog, "hbo_max", "SE", html)
    assert result.status == "ok"
    assert _prices(result) == {
        ("basic_with_ads", "month"): (Decimal("89.00"), "SEK"),
        ("standard", "month"): (Decimal("129.00"), "SEK"),
        ("standard", "year"): (Decimal("1290.00"), "SEK"),
        ("premium", "month"): (Decimal("179.00"), "SEK"),
    }
    flags = {(o.plan_id, o.billing_period): o.flags for o in result.observations}
    assert flags[("premium", "month")] == [FLAG_MARKUP, FLAG_DUPLICATE]
    assert flags[("standard", "year")] == [FLAG_MARKUP]
    assert result.missing_plans == []
    assert any("premium: 2 month prices" in n for n in result.notes)


def test_tr_heading_over_plain_list(catalog):
    result = _parse(catalog, "youtube_premium", "TR", _html("tr_youtube_premium.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        ("individual", "month"): (Decimal("57.99"), "TRY"),
        ("student", "month"): (Decimal("38.99"), "TRY"),
        ("lite", "month"): (Decimal("39.99"), "TRY"),
    }
    # Row numbers ("1", "2") are not part of the names.
    assert {o.plan_label_raw for o in result.observations} >= {"YouTube Premium"}


# ---------------------------------------------------------------- page states


def test_not_available_page(catalog):
    result = _parse(catalog, "hbo_max", "SE", _html("not_available.html"))
    assert result.status == "not_available"
    assert not result.observations


def test_no_iap_section(catalog):
    result = _parse(catalog, "google_one", "US", _html("us_no_iap.html"))
    assert result.status == "no_prices"
    assert not result.observations and not result.llm_requests


def test_http_404_is_not_available(catalog):
    result = _parse(catalog, "hbo_max", "SE", "<html><body>Not Found</body></html>", status=404)
    assert result.status == "not_available"


def test_bot_wall(catalog):
    html = "<html><head><title>Access Denied</title></head><body>Access Denied</body></html>"
    assert _parse(catalog, "hbo_max", "SE", html, status=403).status == "blocked"
    assert _parse(catalog, "hbo_max", "SE", html).status == "blocked"


def test_redirect_to_another_storefront(catalog):
    result = _parse(
        catalog,
        "youtube_premium",
        "SE",
        _html("us_youtube_premium.html"),
        final_url="https://apps.apple.com/us/app/youtube/id544007664",
    )
    assert result.status == "wrong_country"
    assert "/us/" in result.notes[0]


def test_redirect_away_from_the_app(catalog):
    result = _parse(
        catalog,
        "hbo_max",
        "SE",
        _html("se_hbo_max.html"),
        final_url="https://apps.apple.com/se/iphone/today",
    )
    assert result.status == "not_available"


def test_canonical_of_another_storefront(catalog):
    # A replayed or IP-redirected page keeps the requested URL; canonical still says /se/.
    result = _parse(catalog, "youtube_premium", "CO", _html("se_youtube_premium.html"))
    assert result.status == "wrong_country"
    assert "canonical" in result.notes[0]


def test_prices_in_another_currency(catalog):
    html = _html("se_hbo_max.html").replace("/se/app/", "/de/app/")
    result = _parse(catalog, "hbo_max", "DE", html)
    assert result.status == "wrong_country"
    assert not result.observations
    assert any("expected EUR" in n for n in result.notes)


def test_only_unmapped_items_is_partial(catalog):
    html = (
        "<html><body><dl><div><dt>In-App Purchases</dt><dd><ul>"
        "<li><span>HBO Max</span><span>$9.99</span></li>"
        "<li><span>HBO Max</span><span>$99.99</span></li>"
        "</ul></dd></div></dl></body></html>"
    )
    result = _parse(catalog, "hbo_max", "US", html)
    assert result.status == "partial"
    assert not result.observations
    assert len(result.unmapped) == 2
    (req,) = result.llm_requests
    assert req.page_text == "HBO Max — $9.99\nHBO Max — $99.99"


def test_text_pairs_cannot_override_rows():
    # JSON says $18.99; a misaligned visible text block pairs the same name with $15.99.
    html = (
        '<html><body><script type="application/json" id="serialized-server-data">'
        '{"title":"In-App Purchases","items":[{"leadingText":"YouTube Premium",'
        '"trailingText":"$18.99"}]}</script>'
        "<div><h3>In-App Purchases</h3><p>YouTube Premium</p><p>$15.99</p>"
        "<p>YouTube Premium (Family)</p><p>$34.99</p></div></body></html>"
    )
    items, notes = collect_items(html, LexborHTMLParser(html), ["USD"])
    assert [(i.name, i.amount) for i in items] == [
        ("YouTube Premium", Decimal("18.99")),
        ("YouTube Premium (Family)", Decimal("34.99")),
    ]
    assert any("conflicts with rows" in n for n in notes)


def test_json_numeric_price_with_currency():
    html = (
        '<html><body><script type="application/json">{"inAppPurchases":[{"name":"100 GB",'
        '"price":1.99,"currencyCode":"usd"}]}</script></body></html>'
    )
    (item,) = items_from_json(LexborHTMLParser(html), ["USD"])
    assert (item.name, item.amount, item.currency) == ("100 GB", Decimal("1.99"), "USD")


# ---------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    "text",
    [
        "In-App Purchases",
        "In-App Purchase",
        "Compras dentro de la app",
        "Compras dentro de la aplicación",
        "Compras dentro do app",
        "Compras In-App",
        "Compras integradas",
        "Köp inuti appen",
        "In-App-Käufe",
        "Achats intégrés",
        "Acquisti in-app",
        "Uygulama İçi Satın Almalar",
        "In-app-aankopen",
        "Køb i appen",
        "Kjøp i appen",
        "Sovelluksen sisäiset ostot",
        "Zakupy w aplikacji",
        "Nákupy v aplikaci",
        "Alkalmazáson belüli vásárlások",
        "Achiziții în aplicație",
        "Pembelian Dalam Aplikasi",
        "Mua hàng trong ứng dụng",
        "In-App Purchases:",
    ],
)
def test_iap_headings(text):
    assert is_iap_heading(text)


@pytest.mark.parametrize(
    "text",
    [
        "Offers In-App Purchases",
        "Ofrece compras dentro de la app",
        "Erbjuder köp inuti appen",
        "Compras en la App Store",
        "Information",
        "YouTube Premium",
        "",
    ],
)
def test_not_iap_headings(text):
    assert not is_iap_heading(text)


@pytest.mark.parametrize(
    ("name", "period"),
    [
        ("YouTube Premium", None),
        ("2 TB (anual)", "year"),
        ("HBO Max Standard – årsabonnemang", "year"),
        ("Premium (12 months)", "year"),
        ("Premium – 1 year", "year"),
        ("Premium Jahresabo", "year"),
        ("Standard Yearly", "year"),
        ("Premium Monthly", "month"),
        ("Premium 1 mes", "month"),
        ("Premium månadsvis", "month"),
        ("Premium (3 months)", "quarter"),
        ("Annual plan billed monthly", None),
        ("100 GB", None),
        # Disney+ names seen by the probe (2026-10-08) and their siblings in other storefronts.
        ("Disney+ Premium mensual", "month"),
        ("Disney+ Premium anual", "year"),
        ("Disney+ Estándar mensual", "month"),
        ("Disney+ Premium (månad)", "month"),
        ("Disney+ Premium 1 år", "year"),
        ("Disney+ Premium jährlich", "year"),
        ("Disney+ Premium mensile", "month"),
        ("Disney+ Premium annuale", "year"),
        ("Disney+ Premium Monat", "month"),
        ("Disney+ Standard ar reklāmām", None),  # Latvian "with ads", not "år"
        ("Disney+ Premium 3 meses", "quarter"),
        ("HBO Max Estándar trimestral", "quarter"),
    ],
)
def test_name_period(name, period):
    assert name_period(name) == period


def test_infer_periods_pairs_and_duplicates():
    d = Decimal
    assert [p for p, _ in infer_periods([d("149"), d("1490")])] == ["month", "year"]
    assert [p for p, _ in infer_periods([d("18.99"), d("15.99")])] == ["month", "month"]
    # Ratio outside 8-12.5 (e.g. a 6-month plan) is not treated as annual.
    assert [p for p, _ in infer_periods([d("10"), d("60")])] == ["month", "month"]
    # Against a stated annual price.
    assert infer_periods([d("42900")], [(d("429900"), "year")])[0][0] == "month"
    assert infer_periods([d("1290")], [(d("129"), "month")])[0][0] == "year"
    assert infer_periods([d("5")], default="year") == [("year", "default")]


# ---------------------------------------------------------------- Disney+ (probe 2026-10-08)


def test_disney_plus_sweden_from_the_app_store(catalog):
    # Names carry no period; 1 590,00 kr is 10x 159,00 kr, so it is the annual Premium price.
    result = _parse(catalog, "disney_plus", "SE", _html("se_disney_plus.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        ("standard_with_ads", "month"): (Decimal("69.00"), "SEK"),
        ("standard", "month"): (Decimal("109.00"), "SEK"),
        ("premium", "month"): (Decimal("159.00"), "SEK"),
        ("premium", "year"): (Decimal("1590.00"), "SEK"),
    }
    assert result.missing_plans == []
    for o in result.observations:
        assert o.flags == [FLAG_MARKUP]  # repeated rows are the same item, not duplicates
    labels = {o.plan_id: o.plan_label_raw for o in result.observations}
    assert labels["standard_with_ads"] == "Disney+ Standard med reklam"
    assert any("1 590,00 kr read as year" in n for n in result.notes)


def test_disney_plus_colombia_from_the_app_store(catalog):
    result = _parse(catalog, "disney_plus", "CO", _html("co_disney_plus.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        ("standard", "month"): (Decimal("36900.00"), "COP"),
        ("premium", "month"): (Decimal("54900.00"), "COP"),
        ("premium", "year"): (Decimal("459900.00"), "COP"),  # "anual" in the name
    }
    assert result.missing_plans == ["standard_with_ads"]


def test_disney_plus_plan_and_bundle_exclusions(catalog):
    svc = catalog.service("disney_plus")
    [src] = svc.sources
    assert src.adapter == "appstore_iap" and src.channel == "ios_iap"
    tasks = {t.countries[0]: t for t in ADAPTER.plan(svc, src, catalog)}
    assert tasks["SE"].url == "https://apps.apple.com/se/app/id1446075923"
    assert not {"IN", "ID", "PK", "NG", "VN"} & set(tasks)
    html = _html("se_disney_plus.html").replace(
        "<span>Disney+ Standard</span> <span>109,00&nbsp;kr</span>",
        "<span>Disney+, Hulu, Max Bundle Standard</span> <span>209,00&nbsp;kr</span>",
        1,
    )
    result = _parse(catalog, "disney_plus", "SE", html)
    assert _prices(result)[("standard", "month")] == (Decimal("109.00"), "SEK")
    assert any("exclude_patterns" in n for n in result.notes)


# ---------------------------------------------------------------- localized plan names


def _iap_page(rows: list[tuple[str, str]]) -> str:
    dl = "".join(f"<dt>{name}</dt><dd>{price}</dd>" for name, price in rows)
    return f"<html><body><section><h2>In-App Purchases</h2><dl>{dl}</dl></section></body></html>"


def _periods(result):
    return {(o.plan_id, o.billing_period): o.amount for o in result.observations}


@pytest.mark.parametrize(
    ("cc", "rows", "expected"),
    [
        # prices/F1: Portuguese and German student / family names are not Individual.
        (
            "BR",
            [
                ("YouTube Premium", "R$ 26,90"),
                ("YouTube Premium Estudante", "R$ 14,90"),
                ("YouTube Premium Família", "R$ 47,90"),
                ("YouTube Premium Universitário", "R$ 14,90"),
            ],
            {
                ("individual", "month"): Decimal("26.90"),
                ("student", "month"): Decimal("14.90"),
                ("family", "month"): Decimal("47.90"),
            },
        ),
        (
            "PT",
            [("YouTube Premium", "12,99 €"), ("YouTube Premium – Estudantes", "6,99 €")],
            {("individual", "month"): Decimal("12.99"), ("student", "month"): Decimal("6.99")},
        ),
        (
            "DE",
            [("YouTube Premium", "15,99 €"), ("YouTube Premium für Studierende", "8,99 €")],
            {("individual", "month"): Decimal("15.99"), ("student", "month"): Decimal("8.99")},
        ),
    ],
)
def test_youtube_localized_student_and_family_names(catalog, cc, rows, expected):
    result = _parse(catalog, "youtube_premium", cc, _iap_page(rows))
    assert result.status == "ok"
    assert _periods(result) == expected
    assert not result.unmapped and not result.llm_requests


@pytest.mark.parametrize(
    ("name", "plan"),
    [
        ("YouTube Premium Estudante", "student"),
        ("YouTube Premium – Estudantes", "student"),
        ("YouTube Premium für Studierende", "student"),
        ("YouTube Premium Universitário", "student"),
        ("YouTube Premium Família", "family"),
        ("YOUTUBE PREMIUM FAMÍLIA", "family"),
        # Unchanged names from the captured pages.
        ("YouTube Premium (Family)", "family"),
        ("YouTube Premium – familj", "family"),
        ("YouTube Premium (Öğrenci)", "student"),
        ("YouTube Premium – student", "student"),
        ("YouTube Premium Student", "student"),
        ("Premium Lite", "lite"),
        ("YouTube Premium", "individual"),
    ],
)
def test_youtube_iap_patterns(catalog, name, plan):
    assert match_iap_plan(catalog.service("youtube_premium"), name) == [plan]


@pytest.mark.parametrize(
    ("name", "plan"),
    [
        # prices/F2: Estonian, Latvian and French ad-tier names.
        ("Disney+ Standard reklaamidega", "standard_with_ads"),
        ("Disney+ Standarts ar reklāmām", "standard_with_ads"),
        ("Disney+ Standard avec pub", "standard_with_ads"),
        # Unchanged.
        ("Disney+ Standard su reklamomis", "standard_with_ads"),
        ("Disney+ Standard med reklam", "standard_with_ads"),
        ("Disney+ Basic Monthly", "standard_with_ads"),
        ("Disney+ Standarts", "standard"),
        ("Disney+ Standard", "standard"),
        ("Disney+ Estándar mensual", "standard"),
        ("Disney+ Premium", "premium"),
    ],
)
def test_disney_iap_patterns(catalog, name, plan):
    assert match_iap_plan(catalog.service("disney_plus"), name) == [plan]


@pytest.mark.parametrize(
    ("cc", "rows"),
    [
        (
            "EE",
            [
                ("Disney+ Standard reklaamidega", "5,99 €"),
                ("Disney+ Standard", "9,99 €"),
                ("Disney+ Premium", "13,99 €"),
            ],
        ),
        (
            "LV",
            [
                ("Disney+ Standarts ar reklāmām", "5,99 €"),
                ("Disney+ Standarts", "9,99 €"),
                ("Disney+ Premium", "13,99 €"),
            ],
        ),
    ],
)
def test_disney_baltic_ad_tier_is_not_standard(catalog, cc, rows):
    result = _parse(catalog, "disney_plus", cc, _iap_page(rows))
    assert result.status == "ok"
    assert _periods(result) == {
        ("standard_with_ads", "month"): Decimal("5.99"),
        ("standard", "month"): Decimal("9.99"),
        ("premium", "month"): Decimal("13.99"),
    }
    assert result.missing_plans == []


def test_disney_us_listing(catalog):
    # Shape of the live US listing: "Basic" is the ads tier; the annual Premium is 10x monthly.
    rows = [
        ("Disney+ Basic Monthly", "$12.49"),
        ("Disney+ Premium", "$21.49"),
        ("Disney+ Premium", "$214.99"),
        ("Disney Bundle Duo Basic", "$12.99"),
    ]
    result = _parse(catalog, "disney_plus", "US", _iap_page(rows))
    assert _periods(result) == {
        ("standard_with_ads", "month"): Decimal("12.49"),
        ("premium", "month"): Decimal("21.49"),
        ("premium", "year"): Decimal("214.99"),
    }
    assert not result.unmapped and not result.llm_requests


# prices/F1 (defensive): an unknown variant name must not win the plan by being cheapest.
def test_different_names_at_different_prices_go_to_the_llm(catalog):
    rows = [("YouTube Premium", "11,99 €"), ("YouTube Premium tudengitele", "6,99 €")]
    result = _parse(catalog, "youtube_premium", "EE", _iap_page(rows))
    assert result.observations == []
    assert result.status == "partial"
    assert result.missing_plans == ["individual", "duo", "family", "student", "lite"]
    assert [i.name for i in result.unmapped] == [n for n, _ in rows]
    (req,) = result.llm_requests
    assert req.reason == "unmapped_iap"
    assert req.page_text == "YouTube Premium — 11,99 €\nYouTube Premium tudengitele — 6,99 €"
    assert any("sent to the LLM" in n for n in result.notes)


def test_variant_next_to_a_stated_period_goes_to_the_llm_alone(catalog):
    rows = [
        ("YouTube Premium (1 month)", "11,99 €"),
        ("YouTube Premium (12 months)", "119,99 €"),
        ("YouTube Premium tudengitele", "6,99 €"),
    ]
    result = _parse(catalog, "youtube_premium", "EE", _iap_page(rows))
    assert _periods(result) == {
        ("individual", "month"): Decimal("11.99"),
        ("individual", "year"): Decimal("119.99"),
    }
    assert [i.name for i in result.unmapped] == ["YouTube Premium tudengitele"]
    assert result.status == "ok"


def test_one_name_listed_twice_still_keeps_the_lowest(catalog):
    rows = [("YouTube Premium", "11,99 €"), ("YouTube Premium", "13,99 €")]
    result = _parse(catalog, "youtube_premium", "EE", _iap_page(rows))
    assert _periods(result) == {("individual", "month"): Decimal("11.99")}
    assert FLAG_DUPLICATE in result.observations[0].flags
    assert not result.unmapped and not result.llm_requests


# LLM jobs the 2026-10-22 daily run spent on known noise (probe of every storefront).
def test_youtube_drops_rentals_super_chat_and_memberships(catalog):
    rows = [
        ("YouTube Premium", "11,99 €"),
        ("Movies & Shows", "3,99 €"),
        ("Super Thanks", "1,99 €"),
        ("Pase de Oro", "4,99 €"),
    ]
    result = _parse(catalog, "youtube_premium", "EE", _iap_page(rows))
    assert _periods(result) == {("individual", "month"): Decimal("11.99")}
    assert not result.unmapped and not result.llm_requests


def test_deezer_regional_sku_keeps_the_lowest(catalog):
    rows = [
        ("Deezer Premium", "14,99 €"),
        ("Deezer Family", "22,99 €"),
        ("Deezer Family Portugal", "19,99 €"),
    ]
    result = _parse(catalog, "deezer", "PT", _iap_page(rows))
    assert _periods(result) == {
        ("premium", "month"): Decimal("14.99"),
        ("family", "month"): Decimal("19.99"),
    }
    family = next(o for o in result.observations if o.plan_id == "family")
    assert FLAG_DUPLICATE in family.flags
    assert not result.unmapped and not result.llm_requests


def test_chatgpt_pro_keeps_the_200_tier(catalog):
    rows = [
        ("ChatGPT Plus", "$19.99"),
        ("ChatGPT Pro 100", "$100.00"),
        ("ChatGPT Pro 200", "$200.00"),
        ("ChatGPT Pro 500", "$500.00"),
    ]
    result = _parse(catalog, "chatgpt", "US", _iap_page(rows))
    assert _periods(result) == {
        ("plus", "month"): Decimal("19.99"),
        ("pro", "month"): Decimal("200.00"),
    }
    assert not result.unmapped and not result.llm_requests


@pytest.mark.parametrize(
    ("service_id", "name", "plan"),
    [
        ("google_one", "100 Go – Forfait mensuel", "100gb"),
        ("google_one", "200 Go – Forfait annuel", "200gb"),
        ("hbo_max", "Basis mit Werbung", "basic_with_ads"),
        ("hbo_max", "Base con pubblicità", "basic_with_ads"),
        ("hbo_max", "Standard", "standard"),
        ("disney_plus", "Disney+ Prémium", "premium"),
    ],
)
def test_localized_names_map_without_the_llm(catalog, service_id, name, plan):
    assert match_iap_plan(catalog.service(service_id), name) == [plan]


@pytest.mark.parametrize(
    ("service_id", "name"),
    [
        ("duolingo", "Juwelenkarre (3000)"),
        ("duolingo", "Barile di gemme (1200)"),
        ("duolingo", "Barril de Cristais (1200)"),
        ("duolingo", "Kist met Juwelen"),
        ("hbo_max", "Legacy Standard"),
        ("hbo_max", "HBO Max DAZN"),
        ("disney_plus", "Premier Access: Black Widow"),
    ],
)
def test_known_noise_is_excluded(catalog, service_id, name):
    svc = catalog.service(service_id)
    src = next(s for s in svc.sources if s.adapter == "appstore_iap")
    patterns = src.params["exclude_patterns"]
    assert any(re.search(p, name, re.IGNORECASE) for p in patterns)
