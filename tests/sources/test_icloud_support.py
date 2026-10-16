from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from haggler.fetch import RawResponse
from haggler.models.catalog import SourceDef
from haggler.sources.icloud_support import ICloudSupportAdapter, currency_code

FIXTURES = Path(__file__).parent / "fixtures" / "icloud_support"
URL = "https://support.apple.com/en-us/108047"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _resp(text: str, *, final_url: str = URL, status: int = 200) -> RawResponse:
    return RawResponse(url=URL, final_url=final_url, status_code=status, text=text)


def _page(body: str) -> str:
    return f"<!DOCTYPE html><html lang='en-US'><body><main>{body}</main></body></html>"


@pytest.fixture(scope="module")
def adapter() -> ICloudSupportAdapter:
    return ICloudSupportAdapter()


@pytest.fixture(scope="module")
def svc(catalog):
    return catalog.service("icloud_plus")


@pytest.fixture(scope="module")
def task(adapter, svc, catalog):
    [t] = adapter.plan(svc, svc.sources[0], catalog)
    return t


@pytest.fixture(scope="module")
def full(adapter, task, catalog):
    return adapter.parse(task, _resp(_html("108047_en_us.html")), catalog)


def _prices(result, cc: str) -> dict[str, tuple[Decimal, str]]:
    return {o.plan_id: (o.amount, o.currency) for o in result.observations if o.country == cc}


# ---------------------------------------------------------------- plan


def test_plan_is_one_task_for_every_catalog_country(task, catalog):
    assert task.task_id == "icloud_support:icloud_plus:ALL"
    assert task.adapter_id == "icloud_support"
    assert task.service_ids == ["icloud_plus"]
    assert task.countries == catalog.country_codes()
    assert task.url == URL
    assert task.host == "support.apple.com"
    assert task.channel == "web"
    assert "not_configured" not in task.params


def test_plan_honours_source_country_filters(adapter, svc, catalog):
    src = SourceDef(adapter="icloud_support", params={"url": URL}, exclude=["AR", "PE"])
    [t] = adapter.plan(svc, src, catalog)
    assert "AR" not in t.countries and "PE" not in t.countries
    assert len(t.countries) == len(catalog.countries) - 2

    only = SourceDef(adapter="icloud_support", params={"url": URL}, countries=["SE", "LT"])
    [t] = adapter.plan(svc, only, catalog)
    assert t.countries == ["SE", "LT"]


# ---------------------------------------------------------------- full article


def test_full_article_lists_missing_countries(full):
    # The fixture predates the 67-country catalog: the countries added since are not on it.
    assert full.status == "partial"
    assert any("PK" in n and "EG" in n and "BO" in n for n in full.notes)


def test_sweden_all_tiers(full):
    assert _prices(full, "SE") == {
        "50gb": (Decimal("12"), "SEK"),
        "200gb": (Decimal("39"), "SEK"),
        "2tb": (Decimal("129"), "SEK"),
        "6tb": (Decimal("389"), "SEK"),
        "12tb": (Decimal("779"), "SEK"),
    }


@pytest.mark.parametrize(
    ("cc", "plan", "amount", "currency"),
    [
        ("NO", "200gb", "45", "NOK"),
        ("DK", "2tb", "99", "DKK"),
        ("LT", "200gb", "2.99", "EUR"),
        ("LT", "12tb", "59.99", "EUR"),
        ("CO", "200gb", "11900", "COP"),
        ("CO", "12tb", "239900", "COP"),
        ("US", "200gb", "2.99", "USD"),
        ("US", "12tb", "59.99", "USD"),
        ("GB", "2tb", "8.99", "GBP"),
        ("TR", "200gb", "129.99", "TRY"),  # header spelled "Türkiye"
        ("TR", "6tb", "1199.99", "TRY"),
        ("CZ", "12tb", "1499", "CZK"),  # NBSP thousands separator
        ("HU", "2tb", "2990", "HUF"),
        ("CL", "200gb", "2900", "CLP"),  # footnote after "(CLP)"
        ("BR", "200gb", "16.90", "BRL"),  # <sup> footnote inside the name
        ("MX", "12tb", "1099", "MXN"),
        ("RO", "6tb", "149.99", "RON"),  # all tiers on one line
        ("PH", "2tb", "499", "PHP"),  # header and tiers in the same paragraph
        ("VN", "12tb", "1199000", "VND"),
        ("ZA", "200gb", "44.99", "ZAR"),
        ("AR", "200gb", "2.99", "USD"),  # header currency kept; validation decides
    ],
)
def test_country_prices(full, cc, plan, amount, currency):
    assert _prices(full, cc)[plan] == (Decimal(amount), currency)


def test_multi_country_header_fans_out(full):
    for cc in ("FI", "EE", "LV", "DE", "ES", "IE", "NL", "PT"):
        assert _prices(full, cc)["200gb"] == (Decimal("2.99"), "EUR"), cc
        assert len(_prices(full, cc)) == 5


def test_non_catalog_countries_are_ignored(full, task):
    assert {o.country for o in full.observations} <= set(task.countries)
    # Japan's ¥ prices never leak into another country's block.
    assert not [o for o in full.observations if o.amount == Decimal("150")]


def test_footnotes_and_region_headings_do_not_add_tiers(full):
    for cc in ("VN", "US", "GB", "ZA"):
        assert len(_prices(full, cc)) == 5, cc


def test_drafts_are_flagged_and_carry_evidence(full):
    assert full.observations
    for o in full.observations:
        assert o.flags == ["apple_billing"]
        assert o.service_id == "icloud_plus"
        assert o.billing_period == "month"
        assert "|" in o.evidence_snippet
    se_2tb = next(o for o in full.observations if o.country == "SE" and o.plan_id == "2tb")
    assert se_2tb.evidence_snippet == "Sweden (SEK) | 2 TB: 129 kr"
    assert se_2tb.plan_label_raw == "2 TB"
    fi = next(o for o in full.observations if o.country == "FI" and o.plan_id == "50gb")
    assert fi.evidence_snippet.endswith("(EUR) | 50GB: €0.99")
    assert len(fi.evidence_snippet) <= 240


def test_drafts_follow_catalog_order(full, task):
    order = [o.country for o in full.observations]
    first_seen = list(dict.fromkeys(order))
    assert first_seen == [cc for cc in task.countries if cc in first_seen]
    se = [o.plan_id for o in full.observations if o.country == "SE"]
    assert se == ["50gb", "200gb", "2tb", "6tb", "12tb"]


# ---------------------------------------------------------------- other layouts / statuses


def test_table_layout(adapter, task, catalog):
    result = adapter.parse(task, _resp(_html("table_layout.html")), catalog)
    assert result.status == "partial"
    assert _prices(result, "SE")["2tb"] == (Decimal("129"), "SEK")
    assert _prices(result, "NO")["200gb"] == (Decimal("45"), "NOK")
    assert _prices(result, "US")["12tb"] == (Decimal("59.99"), "USD")
    lt = _prices(result, "LT")
    assert lt["6tb"] == (Decimal("29.99"), "EUR")
    assert "12tb" not in lt  # "—" cell
    assert {o.country for o in result.observations} == {"SE", "NO", "LT", "US", "IS"}


def test_few_countries_is_partial(adapter, task, catalog):
    html = _page(
        "<h3>Sweden (SEK)</h3><p>50GB: 12 kr<br>200GB: 39 kr</p>"
        "<h3>Colombia (COP)</h3><ul><li>50GB: $3.900</li><li>2TB: $39.900</li></ul>"
    )
    result = adapter.parse(task, _resp(html), catalog)
    assert result.status == "partial"
    assert _prices(result, "CO") == {
        "50gb": (Decimal("3900"), "COP"),
        "2tb": (Decimal("39900"), "COP"),
    }
    assert any("default plan 200gb missing for: CO" in n for n in result.notes)


def test_page_without_country_blocks_is_no_prices(adapter, task, catalog):
    html = _page("<h1>iCloud+ plans and pricing</h1><p>Upgrade to 50GB, 200GB or 2TB.</p>")
    result = adapter.parse(task, _resp(html), catalog)
    assert result.status == "no_prices"
    assert result.observations == []


def test_tiers_before_any_header_are_ignored(adapter, task, catalog):
    html = _page("<p>50GB: $0.99</p><p>Sweden (SEK)</p><p>200GB: 39 kr</p>")
    result = adapter.parse(task, _resp(html), catalog)
    assert [(o.country, o.plan_id) for o in result.observations] == [("SE", "200gb")]


def test_block_closes_after_unrelated_text(adapter, task, catalog):
    html = _page(
        "<p><b>Sweden (SEK)</b></p><p>50GB: 12 kr</p><h2>Asia Pacific</h2><p>200GB: $2.99</p>"
    )
    result = adapter.parse(task, _resp(html), catalog)
    assert [(o.country, o.plan_id) for o in result.observations] == [("SE", "50gb")]


def test_price_in_other_explicit_currency_is_rejected(adapter, task, catalog):
    html = _page("<p><b>Sweden (SEK)</b></p><p>50GB: US$0.99<br>200GB: 39 kr</p>")
    result = adapter.parse(task, _resp(html), catalog)
    assert _prices(result, "SE") == {"200gb": (Decimal("39"), "SEK")}


def test_bare_amount_uses_header_currency(adapter, task, catalog):
    html = _page("<p><b>Norway (NOK)</b></p><p>50GB: 15<br>200GB: 45</p>")
    result = adapter.parse(task, _resp(html), catalog)
    assert _prices(result, "NO") == {
        "50gb": (Decimal("15"), "NOK"),
        "200gb": (Decimal("45"), "NOK"),
    }


def test_duplicate_country_keeps_first_value_and_notes_conflict(adapter, task, catalog):
    html = _page(
        "<p><b>Sweden (SEK)</b></p><p>200GB: 39 kr</p><p><b>Sweden (SEK)</b></p><p>200GB: 49 kr</p>"
    )
    result = adapter.parse(task, _resp(html), catalog)
    assert _prices(result, "SE") == {"200gb": (Decimal("39"), "SEK")}
    assert any("SE 200gb" in n for n in result.notes)


def test_countries_outside_task_are_dropped(adapter, svc, catalog):
    src = SourceDef(adapter="icloud_support", params={"url": URL}, countries=["SE"])
    [t] = adapter.plan(svc, src, catalog)
    result = adapter.parse(t, _resp(_html("108047_en_us.html")), catalog)
    assert {o.country for o in result.observations} == {"SE"}
    assert result.status == "ok"


def test_unicode_and_sup_footnotes_in_header(adapter, task, catalog):
    html = _page(
        "<p><b>Lithuania²³ (EUR)</b></p><p>200GB: €2.99</p>"
        "<p><b>Türkiye<sup>4</sup> (TRY)</b></p><p>200GB: ₺129,99</p>"
    )
    result = adapter.parse(task, _resp(html), catalog)
    assert _prices(result, "LT") == {"200gb": (Decimal("2.99"), "EUR")}
    assert _prices(result, "TR") == {"200gb": (Decimal("129.99"), "TRY")}


def test_blocked_page(adapter, task, catalog):
    html = (
        "<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>"
        "You don't have permission to access this server.<p>Reference #18.4f2</p></body></html>"
    )
    assert adapter.parse(task, _resp(html, status=200), catalog).status == "blocked"
    assert adapter.parse(task, _resp("", status=403), catalog).status == "blocked"


def test_redirect_to_other_locale_is_noted(adapter, task, catalog):
    html = _page("<p><b>Sverige (SEK)</b></p><p>200 GB: 39 kr</p>")
    result = adapter.parse(
        task, _resp(html, final_url="https://support.apple.com/sv-se/108047"), catalog
    )
    assert result.status == "no_prices"
    assert any("en-us" in n for n in result.notes)


def test_footnote_sup_after_price_is_not_part_of_the_number(adapter, task, catalog):
    html = _page(
        "<p><b>United States<sup>3</sup> (USD)</b></p>"
        "<p>50GB: $0.99<sup>4</sup><br>12TB: $59.99<sup>4</sup><br>2TB: 1 299 kr</p>"
    )
    result = adapter.parse(task, _resp(html), catalog)
    # "1 299 kr" cannot be USD and is dropped.
    assert _prices(result, "US") == {
        "50gb": (Decimal("0.99"), "USD"),
        "12tb": (Decimal("59.99"), "USD"),
    }


# ---------------------------------------------------------------- 2026 layout (probe-verified)

EURO_WITHOUT_OWN_ROW = (
    *("FI", "LT", "LV", "EE", "DE", "ES", "NL", "PT", "AT", "BE", "FR", "IT", "LU", "GR"),
    *("CY", "MT", "SK", "SI", "BG", "ME"),
)


@pytest.fixture(scope="module")
def tables(adapter, task, catalog):
    return adapter.parse(task, _resp(_html("108047_2026_tables.html")), catalog)


def test_2026_tables_currency_names(tables):
    assert _prices(tables, "SE")["200gb"] == (Decimal("39"), "SEK")  # "(Swedish Krona)"
    assert _prices(tables, "TR")["6tb"] == (Decimal("1199.99"), "TRY")  # "(Turkish Lira)"
    assert _prices(tables, "GB")["2tb"] == (Decimal("8.99"), "GBP")  # "(British Pound)"
    assert _prices(tables, "CO")["200gb"] == (Decimal("11900"), "COP")  # "(Colombian Peso)"
    assert _prices(tables, "MX")["12tb"] == (Decimal("1099"), "MXN")  # plural "(Mexican Pesos)"
    assert _prices(tables, "AR")["200gb"] == (Decimal("2.99"), "USD")  # "(US Dollar)"
    assert _prices(tables, "VN")["12tb"] == (Decimal("1199000"), "VND")
    assert _prices(tables, "CH")["50gb"] == (Decimal("1.00"), "CHF")  # ISO code still works
    assert len(_prices(tables, "US")) == 5


def test_2026_euro_row_covers_euro_countries_without_their_own_row(tables):
    for cc in EURO_WITHOUT_OWN_ROW:
        assert _prices(tables, cc) == {
            "50gb": (Decimal("0.99"), "EUR"),
            "200gb": (Decimal("2.99"), "EUR"),
            "2tb": (Decimal("9.99"), "EUR"),
            "6tb": (Decimal("29.99"), "EUR"),
            "12tb": (Decimal("59.99"), "EUR"),
        }, cc
    fi = next(o for o in tables.observations if o.country == "FI" and o.plan_id == "200gb")
    assert fi.evidence_snippet == "Euro (Euro) | 200 GB: 2.99 €"
    note = next(n for n in tables.notes if n.startswith("EUR row applied"))
    assert note.endswith(", ".join(EURO_WITHOUT_OWN_ROW))


def test_2026_own_row_beats_the_euro_row(tables):
    # Ireland has a row of its own (listed before "Euro").
    assert _prices(tables, "IE")["200gb"] == (Decimal("3.49"), "EUR")
    assert "IE" not in next(n for n in tables.notes if n.startswith("EUR row applied"))
    # Other rows (Croatia's own, Cameroon, Japan) never leak into other countries.
    assert not [
        o for o in tables.observations if o.amount in (Decimal("3.49"),) and o.country != "IE"
    ]
    assert not [o for o in tables.observations if o.currency == "JPY"]


def test_2026_tables_status_and_missing_countries(tables, task):
    found = {o.country for o in tables.observations}
    assert found == {
        "AR",
        "CO",
        "MX",
        "US",
        "IE",
        "SE",
        "CH",
        "TR",
        "GB",
        "VN",
        "HR",
        *EURO_WITHOUT_OWN_ROW,
    }
    assert tables.status == "partial"  # 31 of 67: the rest of this fixture is illustrative
    assert any(n.startswith("countries not on page:") and "NO" in n for n in tables.notes)


def test_euro_row_before_a_country_row_still_loses(adapter, task, catalog):
    html = _page(
        "<p><b>Euro (Euro)</b></p><p>50GB: 0.99 €<br>200GB: 2.99 €</p>"
        "<p><b>Finland (EUR)</b></p><p>200GB: 3.99 €</p>"
    )
    result = adapter.parse(task, _resp(html), catalog)
    assert _prices(result, "FI") == {"200gb": (Decimal("3.99"), "EUR")}  # no 50gb from "Euro"
    assert _prices(result, "LT") == {
        "50gb": (Decimal("0.99"), "EUR"),
        "200gb": (Decimal("2.99"), "EUR"),
    }


@pytest.mark.parametrize(
    "name", ["Euro", "Eurozone", "Euro area", "Other euro countries", "Euro countries"]
)
def test_currency_region_spellings(adapter, task, catalog, name):
    html = _page(
        f"<table><tr><th>Country (Currency)</th><th>50 GB</th><th>200 GB</th></tr>"
        f"<tr><td>{name}¹ (Euro)</td><td>0.99 €</td><td>2.99 €</td></tr></table>"
    )
    result = adapter.parse(task, _resp(html), catalog)
    assert _prices(result, "DE")["200gb"] == (Decimal("2.99"), "EUR")


def test_unknown_currency_name_falls_back_to_catalog_currency_with_a_note(adapter, task, catalog):
    html = _page(
        "<table><tr><th>Country (Currency)</th><th>50 GB</th><th>200 GB</th></tr>"
        "<tr><td>Sweden (Kronor)</td><td>12 kr</td><td>39 kr</td></tr></table>"
    )
    result = adapter.parse(task, _resp(html), catalog)
    assert _prices(result, "SE")["200gb"] == (Decimal("39"), "SEK")
    assert any("not recognised; assumed SEK" in n for n in result.notes)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Euro", "EUR"),
        ("euros", "EUR"),
        ("US Dollar", "USD"),
        ("US dollars", "USD"),
        ("Swedish Krona", "SEK"),
        ("Swedish kronor", "SEK"),
        ("Turkish Lira", "TRY"),
        ("Pound Sterling", "GBP"),
        ("Vietnamese Dong", "VND"),
        ("SEK", "SEK"),
        ("XYZ", None),  # not an ISO code
        ("Currency", None),
        ("see note", None),
    ],
)
def test_currency_code(catalog, text, code):
    assert currency_code(text, catalog) == code
