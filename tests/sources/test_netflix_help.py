from decimal import Decimal
from pathlib import Path

import pytest

from haggler.fetch import RawResponse
from haggler.sources.netflix_help import NetflixHelpAdapter

FIX = Path(__file__).parent / "fixtures" / "netflix_help"
ADAPTER = NetflixHelpAdapter()


def _task(catalog, cc, **params):
    svc = catalog.service("netflix")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    task = next(t for t in ADAPTER.plan(svc, src, catalog) if t.countries == [cc])
    if params:
        task = task.model_copy(update={"params": {**task.params, **params}})
    return task


def _parse(catalog, cc, html, *, final_url=None, status=200, **params):
    task = _task(catalog, cc, **params)
    resp = RawResponse(url=task.url, final_url=final_url or task.url, status_code=status, text=html)
    return ADAPTER.parse(task, resp, catalog)


def _prices(result):
    return {o.plan_id: (o.amount, o.currency) for o in result.observations}


def _article(*items: str) -> str:
    lis = "".join(f"<li><p>{x}</p></li>" for x in items)
    return (
        "<html><head><title>Plans and Pricing | Netflix Help Center</title></head><body>"
        f'<div class="kb-article"><h1 class="kb-title">Plans and Pricing</h1><ul>{lis}</ul>'
        "</div></body></html>"
    )


def test_plan_one_task_per_country(catalog):
    svc = catalog.service("netflix")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    tasks = ADAPTER.plan(svc, src, catalog)
    assert len(tasks) == len(catalog.countries)
    se = next(t for t in tasks if t.countries == ["SE"])
    assert se.url == "https://help.netflix.com/en/node/24926/se"
    assert se.host == "help.netflix.com"
    assert se.kind == "official"
    assert "not_configured" not in se.params


def test_sweden(catalog):
    result = _parse(catalog, "SE", (FIX / "se.html").read_text(encoding="utf-8"))
    assert result.status == "ok"
    assert _prices(result) == {
        "basic": (Decimal("129"), "SEK"),
        "standard": (Decimal("169"), "SEK"),
        "premium": (Decimal("219"), "SEK"),
    }
    # "Extra member: 59 kr / month each" is not a plan.
    assert all(o.amount != Decimal("59") for o in result.observations)
    std = next(o for o in result.observations if o.plan_id == "standard")
    assert std.country == "SE" and std.service_id == "netflix" and std.billing_period == "month"
    assert "169 kr" in std.evidence_snippet
    assert {"mobile", "standard_with_ads"} <= set(result.missing_plans)
    assert "strategy=colon" in result.notes


def test_colombia_english_digit_grouping(catalog):
    result = _parse(catalog, "CO", (FIX / "co.html").read_text(encoding="utf-8"))
    assert result.status == "ok"
    assert _prices(result) == {
        "basic": (Decimal("18900"), "COP"),
        "standard": (Decimal("29900"), "COP"),
        "premium": (Decimal("44900"), "COP"),
    }


def test_page_fell_back_to_ip_country(catalog):
    html = (FIX / "se_us_fallback.html").read_text(encoding="utf-8")
    result = _parse(catalog, "SE", html)
    assert result.status == "wrong_country"
    assert result.observations == []
    assert any("USD" in n for n in result.notes)


def test_final_url_for_another_country(catalog):
    html = (FIX / "se.html").read_text(encoding="utf-8")
    result = _parse(catalog, "SE", html, final_url="https://help.netflix.com/en/node/24926/us")
    assert result.status == "wrong_country"


def test_final_url_without_country_is_judged_by_currency(catalog):
    html = (FIX / "se.html").read_text(encoding="utf-8")
    result = _parse(catalog, "SE", html, final_url="https://help.netflix.com/en/node/24926")
    assert result.status == "ok"


def test_with_ads_beats_plain_label_and_extra_members_skipped(catalog):
    html = _article(
        "<strong>Standard with ads</strong>: USD 7.99 / month",
        "<strong>Standard</strong>: USD 17.99 / month",
        "<strong>Premium</strong>: USD 24.99 / month",
        "<strong>Extra member with ads</strong>: USD 6.99 / month each",
        "<strong>Extra member</strong>: USD 8.99 / month each",
    )
    result = _parse(catalog, "US", html)
    assert result.status == "ok"
    assert _prices(result) == {
        "standard_with_ads": (Decimal("7.99"), "USD"),
        "standard": (Decimal("17.99"), "USD"),
        "premium": (Decimal("24.99"), "USD"),
    }


def test_several_plans_in_one_paragraph(catalog):
    html = (
        "<html><body><p><b>Mobile</b>: ₹149 / month<br><b>Basic</b>: ₹199 / month<br>"
        "<b>Standard</b>: ₹499 / month<br><b>Premium</b>: ₹649 / month</p></body></html>"
    )
    result = _parse(catalog, "IN", html)
    assert _prices(result) == {
        "mobile": (Decimal("149"), "INR"),
        "basic": (Decimal("199"), "INR"),
        "standard": (Decimal("499"), "INR"),
        "premium": (Decimal("649"), "INR"),
    }


def test_label_and_price_in_separate_blocks(catalog):
    html = (
        "<html><body><div class='plan'><h4>Basic</h4><p>129 kr / month</p></div>"
        "<div class='plan'><h4>Standard</h4><p>169 kr / month</p></div>"
        "<div class='plan'><h4>Premium</h4><p>219 kr / month</p></div></body></html>"
    )
    result = _parse(catalog, "SE", html)
    assert result.status == "ok"
    assert _prices(result)["standard"] == (Decimal("169"), "SEK")
    assert "strategy=cards" in result.notes


def test_default_plan_missing_is_partial(catalog):
    result = _parse(catalog, "SE", _article("<b>Premium</b>: 219 kr / month"))
    assert result.status == "partial"
    assert "standard" in result.missing_plans
    assert _prices(result) == {"premium": (Decimal("219"), "SEK")}


def test_currency_override_from_source_params(catalog):
    html = _article(
        "<b>Basic</b>: €5.99 / month",
        "<b>Standard</b>: €10.99 / month",
        "<b>Premium</b>: €13.99 / month",
    )
    # Netflix bills Romania in EUR; without a pin the page looks like another country's.
    assert _parse(catalog, "RO", html).status == "wrong_country"
    pinned = _parse(catalog, "RO", html, currency_overrides={"RO": "EUR"})
    assert pinned.status == "ok"
    assert _prices(pinned)["standard"] == (Decimal("10.99"), "EUR")


@pytest.mark.parametrize(
    ("html", "status"),
    [
        (
            "<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>"
            "<p>You don't have permission to access this page. Reference #18.5f3e</p>"
            "</body></html>",
            "blocked",
        ),
        (
            "<html><body><h1>Plans and Pricing</h1><p>Choose the plan that's right for you.</p>"
            "</body></html>",
            "no_prices",
        ),
    ],
)
def test_pages_without_prices(catalog, html, status):
    result = _parse(catalog, "SE", html)
    assert result.status == status
    assert result.observations == []


def test_http_403_is_blocked(catalog):
    assert _parse(catalog, "SE", "<html><body>Forbidden</body></html>", status=403).status == (
        "blocked"
    )
