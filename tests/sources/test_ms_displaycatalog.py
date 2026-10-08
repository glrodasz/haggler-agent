import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from haggler.fetch import RawResponse
from haggler.models.catalog import SourceDef
from haggler.sources.ms_displaycatalog import MsDisplayCatalogAdapter, _period_from_props
from haggler.sources.registry import get_adapter

FIX = Path(__file__).parent / "fixtures" / "ms_displaycatalog"
ADAPTER = MsDisplayCatalogAdapter()
FETCHED = datetime(2026, 10, 8, 4, 17, tzinfo=UTC)


def _json(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _task(catalog, cc):
    svc = catalog.service("microsoft_365")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    return next(t for t in ADAPTER.plan(svc, src, catalog) if t.countries == [cc])


def _parse(catalog, cc, text, *, status=200, fetched_at=FETCHED):
    task = _task(catalog, cc)
    resp = RawResponse(
        url=task.url,
        final_url=task.url,
        status_code=status,
        text=text,
        content_type="application/json",
        fetched_at=fetched_at,
    )
    return ADAPTER.parse(task, resp, catalog)


def _prices(result):
    return {(o.plan_id, o.billing_period): (o.amount, o.currency) for o in result.observations}


def test_registered():
    assert isinstance(get_adapter("ms_displaycatalog"), MsDisplayCatalogAdapter)


def test_plan_one_json_task_per_market(catalog):
    svc = catalog.service("microsoft_365")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    tasks = ADAPTER.plan(svc, src, catalog)
    assert len(tasks) == len(catalog.countries)
    se = next(t for t in tasks if t.countries == ["SE"])
    assert se.url == (
        "https://displaycatalog.mp.microsoft.com/v7.0/products"
        "?bigIds=CFQ7TTC0K5BF,CFQ7TTC0K5DM&market=SE&languages=en-US"
    )
    assert se.host == "displaycatalog.mp.microsoft.com"
    assert se.params["accept"] == "application/json"
    assert se.params["big_ids"] == {"personal": "CFQ7TTC0K5BF", "family": "CFQ7TTC0K5DM"}
    assert se.channel == "web" and se.kind == "official"
    assert "not_configured" not in se.params


def test_plan_without_big_ids_is_not_configured(catalog):
    svc = catalog.service("microsoft_365")
    src = SourceDef(adapter="ms_displaycatalog", countries=["SE", "CO"])
    tasks = ADAPTER.plan(svc, src, catalog)
    assert [t.countries[0] for t in tasks] == ["SE", "CO"]
    assert all(t.params["not_configured"] == ["big_ids"] and t.url == "" for t in tasks)


def test_sweden(catalog):
    result = _parse(catalog, "SE", _json("se.json"))
    assert result.status == "ok"
    assert _prices(result) == {
        # Period from SKU properties (RecurrencePolicy.BillingPeriod = P1M) ...
        ("personal", "month"): (Decimal("105"), "SEK"),
        # ... from the SKU description ("One-year subscription") ...
        ("personal", "year"): (Decimal("1049"), "SEK"),
        # ... and from SKU titles ("Monthly", "12-month").
        ("family", "month"): (Decimal("139"), "SEK"),
        # 999 is not purchasable, 1499 is a pricier availability of the same SKU; Classic
        # (1049) and the free trial are skipped.
        ("family", "year"): (Decimal("1399"), "SEK"),
    }
    assert result.missing_plans == []
    fam = next(
        o for o in result.observations if o.plan_id == "family" and o.billing_period == "year"
    )
    assert fam.plan_label_raw == "Microsoft 365 Family – 12-month subscription"
    assert "ListPrice 1399 SEK" in fam.evidence_snippet and "SKU 0002" in fam.evidence_snippet
    assert fam.flags == [] and fam.country == "SE" and fam.service_id == "microsoft_365"
    notes = " | ".join(result.notes)
    assert "Classic" in notes and "TaxType=TaxesIncluded" in notes


def test_expired_availability_is_ignored_without_fetch_time(catalog):
    # Replays may lack fetched_at: the payload's LastModifiedDate still dates the 699 kr promo
    # (ended 2024-01-31) as expired.
    result = _parse(catalog, "SE", _json("se.json"), fetched_at=None)
    assert _prices(result)[("personal", "year")] == (Decimal("1049"), "SEK")


def test_colombia_periods_from_price_ratio(catalog):
    result = _parse(catalog, "CO", _json("co.json"))
    assert result.status == "ok"
    assert _prices(result) == {
        # Two Family SKUs with identical titles: 449 900 is ~10x 44 900.
        ("family", "month"): (Decimal("44900"), "COP"),
        ("family", "year"): (Decimal("449900"), "COP"),
        # A lone SKU with no period hint falls back to params.default_period ("year").
        ("personal", "year"): (Decimal("255920"), "COP"),
    }
    notes = " | ".join(result.notes)
    assert "unmapped product CFQ7TTC0PBMB" in notes
    assert "below MSRP 319900" in notes
    assert "period assumed year" in notes


def test_market_without_products(catalog):
    result = _parse(catalog, "NG", _json("empty_products.json"))
    assert result.status == "not_available"
    assert not result.observations


def test_currency_of_another_market(catalog):
    data = _json("co.json").replace('"CurrencyCode":"COP"', '"CurrencyCode":"USD"')
    result = _parse(catalog, "CO", data)
    assert result.status == "wrong_country"
    assert any("expected COP" in n for n in result.notes)


def test_only_one_plan_is_partial(catalog):
    data = json.loads(_json("se.json"))
    data["Products"] = [p for p in data["Products"] if p["ProductId"] == "CFQ7TTC0K5BF"]
    result = _parse(catalog, "SE", json.dumps(data))
    assert result.status == "partial"
    assert result.missing_plans == ["family"]


def test_nothing_purchasable(catalog):
    data = _json("co.json").replace('"Purchase",', "")
    result = _parse(catalog, "CO", data)
    assert result.status == "not_available"
    assert not result.observations


def test_single_product_response(catalog):
    data = json.loads(_json("se.json"))
    family = next(p for p in data["Products"] if p["ProductId"] == "CFQ7TTC0K5DM")
    result = _parse(catalog, "SE", json.dumps({"Product": family}))
    assert result.status == "ok"
    assert ("family", "year") in _prices(result)


@pytest.mark.parametrize(
    ("status", "text", "expected"),
    [
        (403, "<html><body>Access Denied</body></html>", "blocked"),
        (200, "<html><body>Please solve this captcha</body></html>", "blocked"),
        (200, "<html><body>Service unavailable</body></html>", "no_prices"),
        (404, "", "not_available"),
        (400, '{"code":"BadRequest","message":"Invalid market"}', "no_prices"),
    ],
)
def test_error_responses(catalog, status, text, expected):
    assert _parse(catalog, "SE", text, status=status).status == expected


@pytest.mark.parametrize(
    ("props", "period"),
    [
        ({"RecurrencePolicy": {"BillingPeriod": "P1M"}}, "month"),
        ({"SubscriptionPeriod": "P12M"}, "year"),
        ({"Recurrence": {"DurationUnit": "Year", "DurationValue": 1}}, "year"),
        ({"Recurrence": {"DurationUnit": "Months", "DurationValue": 3}}, "quarter"),
        ({"DurationInDays": 30}, "month"),
        ({"RenewalPeriod": "Annual"}, "year"),
        ({"InstallationTerms": "InstallationTermsStandard", "IsTrial": False}, None),
        (None, None),
    ],
)
def test_period_from_props(props, period):
    assert _period_from_props(props) == period
