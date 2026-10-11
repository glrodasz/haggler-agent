"""Bundle savings (haggler.bundles.pricing): the cost fields of bundles.json v2."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from haggler.bundles.pricing import counted_components, price_bundle, regular_price
from haggler.catalog import load_bundles
from haggler.models.curated import Bundle
from haggler.models.fx import FxRate, FxTable
from haggler.models.observations import PERIOD_MONTHS, PublishedPrice, obs_key

TODAY = date(2026, 10, 10)
FX = FxTable(
    as_of=TODAY,
    rates={
        cur: FxRate(currency=cur, per_eur=Decimal(v), source="ecb", as_of=TODAY)
        for cur, v in {"SEK": "11", "COP": "4500"}.items()
    },
)


def offer(
    service: str,
    plan: str,
    cc: str,
    amount: str,
    currency: str,
    *,
    period: str = "month",
    channel: str = "web",
    status: str = "accepted",
) -> PublishedPrice:
    value = Decimal(amount)
    return PublishedPrice(
        obs_key=obs_key(service, plan, cc, channel, period),
        service_id=service,
        plan_id=plan,
        plan_label_raw=plan,
        country=cc,
        currency=currency,
        amount=value,
        billing_period=period,
        monthly_equiv=(value / PERIOD_MONTHS[period]).quantize(Decimal("0.01")),
        vat_included=True,
        channel=channel,
        source_url="https://example.test/",
        source_kind="official",
        adapter_id="test",
        extraction="parser",
        confidence=0.9,
        status=status,
        first_seen=TODAY,
        last_changed=TODAY,
    )


def bundle(**overrides: Any) -> Bundle:
    data: dict[str, Any] = {
        "bundle_id": "b",
        "provider_id": "acme",
        "provider_type": "marketplace",
        "country": "CO",
        "name": "Acme Total",
        "tier": "total",
        "official_url": "https://www.acme.com.co/planes",
        "price": {"amount": "27000", "currency": "COP", "period": "month"},
        "components": [
            {"service_id": "netflix", "plan_id": "standard", "inclusion": "included"},
            {"service_id": "spotify", "plan_id": "individual", "inclusion": "included"},
        ],
        "last_verified": "2026-10-01",
    }
    data.update(overrides)
    return Bundle.model_validate(data)


CO_ROWS = [
    offer("netflix", "standard", "CO", "26900", "COP"),  # 5.98 EUR
    offer("netflix", "basic", "CO", "16900", "COP"),  # 3.76 EUR
    offer("spotify", "individual", "CO", "16900", "COP"),  # 3.76 EUR
    offer("hbo_max", "basic_with_ads", "CO", "18000", "COP"),  # 4.00 EUR
]


def price(b: Bundle, rows=CO_ROWS, **kw: Any):
    kw.setdefault("residence_currency", "SEK")
    kw.setdefault("fx_fee_pct", 2.0)
    kw.setdefault("today", TODAY)
    return price_bundle(b, rows, FX, **kw)


def test_full_price_saving_and_fx_fee() -> None:
    p = price(bundle())
    assert p.saving_basis == "full"
    assert p.monthly_eur == p.full_monthly_eur == Decimal("6.00")  # 27000 / 4500
    # COP is not the residence's currency: the card FX fee of compare.cheapest (2 %).
    assert p.monthly_eur_with_fee == Decimal("6.12")
    # The sum of the rounded figures the card lists: 26900 / 4500 + 16900 / 4500 = 5.98 + 3.76
    # (9.73 unrounded), so a reader can add them up.
    assert p.component_eur == (Decimal("5.98"), Decimal("3.76"))
    assert p.standalone_sum_eur == Decimal("9.74")
    assert p.standalone_missing == ()
    # From the rounded figures: (9.74 - 6.00) / 9.74; the fee never enters it.
    assert p.saving_pct == Decimal("38.40")


def test_home_currency_has_no_fee() -> None:
    p = price(bundle(), residence_currency="COP")
    assert p.monthly_eur_with_fee == p.monthly_eur


def test_marginal_basis_on_top_of_a_base_plan() -> None:
    telia = bundle(
        country="SE",
        price={
            "amount": "699",
            "currency": "SEK",
            "base_plan_name": "Telia Obegränsad Plus",
            "base_plan_amount": "569",
            "marginal_amount": "130",
        },
    )
    rows = [
        offer("netflix", "standard", "SE", "149", "SEK"),
        offer("spotify", "individual", "SE", "139", "SEK"),
    ]
    p = price(telia, rows)
    assert p.saving_basis == "marginal"
    assert p.marginal_amount == Decimal("130")
    assert p.monthly_eur == Decimal("11.82")  # 130 / 11: the cost on top of the base plan
    assert p.full_monthly_eur == Decimal("63.55")  # 699 / 11: what the card leads with
    assert p.monthly_eur_with_fee == p.monthly_eur  # SEK at home
    assert p.component_eur == (Decimal("13.55"), Decimal("12.64"))
    assert p.standalone_sum_eur == Decimal("26.19")  # 13.55 + 12.64 (26.18 unrounded)
    assert p.saving_pct == Decimal("54.87")


def test_marginal_is_derived_from_the_base_plan_when_not_typed() -> None:
    b = bundle(
        price={
            "amount": "30000",
            "currency": "COP",
            "base_plan_name": "Plan base",
            "base_plan_amount": "21000",
        }
    )
    p = price(b)
    assert (p.saving_basis, p.marginal_amount, p.monthly_eur) == (
        "marginal",
        Decimal("9000"),
        Decimal("2.00"),
    )


def test_yearly_price_counts_per_month() -> None:
    p = price(bundle(price={"amount": "324000", "currency": "COP", "period": "year"}))
    assert p.monthly_eur == Decimal("6.00")  # 324000 / 12 / 4500
    assert p.regular.period == "year"


def test_observed_price_from_the_daily_data() -> None:
    apple = bundle(
        country="SE",
        price={
            "currency": "SEK",
            "source": "observed",
            "observed_service_id": "apple_one",
            "observed_plan_id": "individual",
        },
        components=[
            {"service_id": "apple_music", "plan_id": "individual", "inclusion": "included"}
        ],
    )
    rows = [
        offer("apple_one", "individual", "SE", "2500", "SEK", period="year"),
        offer("apple_one", "individual", "SE", "229", "SEK", channel="ios_iap"),
        offer("apple_one", "individual", "SE", "215", "SEK"),
        offer("apple_one", "individual", "CO", "45900", "COP"),
        offer("apple_music", "individual", "SE", "119", "SEK"),
    ]
    # Web before App Store, monthly before yearly, and only the bundle's own country.
    reg = regular_price(apple, rows, as_of=TODAY)
    assert (reg.amount, reg.currency, reg.period, reg.observed_on) == (
        Decimal("215"),
        "SEK",
        "month",
        TODAY,
    )
    yearly = regular_price(apple, rows[:1], as_of=TODAY)
    assert (yearly.amount, yearly.period) == (Decimal("2500"), "year")
    # A row the pipeline has not accepted is not a price.
    held = [offer("apple_one", "individual", "SE", "215", "SEK", status="quarantined")]
    assert regular_price(apple, held).amount is None
    p = price(apple, [])
    assert p.regular.amount is None and p.regular.observed_on is None
    assert (p.monthly_eur, p.full_monthly_eur, p.monthly_eur_with_fee) == (None, None, None)
    assert p.standalone_missing == ("apple_music",)


def test_intro_never_enters_the_saving() -> None:
    b = bundle(
        price={"amount": "27000", "currency": "COP", "intro_amount": "9000", "intro_months": 3}
    )
    unverified = price(b)
    assert unverified.monthly_eur == Decimal("6.00")
    assert unverified.saving_pct == Decimal("38.40")
    assert unverified.first_year_monthly_eur is None  # only for a verified bundle
    verified = price(b, verified=True)
    assert verified.monthly_eur == Decimal("6.00") and verified.saving_pct == Decimal("38.40")
    # (9000 x 3 + 27000 x 9) / 12 / 4500
    assert verified.first_year_monthly_eur == Decimal("5.00")


def test_first_year_needs_a_current_intro() -> None:
    past = bundle(
        price={
            "amount": "27000",
            "currency": "COP",
            "intro_amount": "9000",
            "intro_months": 3,
            "intro_valid_until": "2026-10-09",
        }
    )
    assert price(past, verified=True).first_year_monthly_eur is None
    long = bundle(
        price={"amount": "27000", "currency": "COP", "intro_amount": "9000", "intro_months": 18}
    )
    assert price(long, verified=True).first_year_monthly_eur == Decimal("2.00")  # 12 intro months


def test_first_year_on_the_marginal_basis() -> None:
    b = bundle(
        price={
            "amount": "30000",
            "currency": "COP",
            "base_plan_name": "Plan base",
            "base_plan_amount": "21000",
            "marginal_amount": "9000",
            "intro_amount": "21000",
            "intro_months": 6,
        }
    )
    # Six months with nothing on top of the base plan, then 9000: (0 x 6 + 9000 x 6) / 12.
    assert price(b, verified=True).first_year_monthly_eur == Decimal("1.00")


def test_choose_n_counts_the_n_cheapest_options() -> None:
    group = [
        {"service_id": "netflix", "plan_id": "standard", "inclusion": "choose_n",
         "choice_group_id": "g", "choose_n": 2},
        {"service_id": "spotify", "plan_id": "individual", "inclusion": "choose_n",
         "choice_group_id": "g", "choose_n": 2},
        {"service_id": "hbo_max", "plan_id": "basic_with_ads", "inclusion": "choose_n",
         "choice_group_id": "g", "choose_n": 2},
    ]  # fmt: skip
    b = bundle(components=group)
    p = price(b)
    # Spotify 3.76 + HBO Max 4.00: the lower bound of what two options cost on their own.
    assert p.standalone_sum_eur == Decimal("7.76")
    assert [c.service_id for c in p.counted] == ["spotify", "hbo_max"]
    # Without prices the first N options in YAML order are counted (and reported missing).
    q = price(b, [])
    assert [c.service_id for c in q.counted] == ["netflix", "spotify"]
    assert q.standalone_missing == ("netflix", "spotify") and q.standalone_sum_eur is None
    assert [c.service_id for c in counted_components(b)] == ["netflix", "spotify"]


def test_partial_sum_lists_the_missing_components() -> None:
    b = bundle(
        components=[
            {"service_id": "netflix", "plan_id": "standard", "inclusion": "included"},
            {"service_id": "apple_arcade", "plan_id": "standard", "inclusion": "included"},
        ]
    )
    p = price(b)
    assert p.standalone_sum_eur == Decimal("5.98")
    assert p.standalone_missing == ("apple_arcade",)
    assert p.saving_pct == Decimal("-0.33")  # (5.98 - 6.00) / 5.98: a lower bound, shown as is


def test_optional_savings_never_count_in_saving_pct() -> None:
    b = bundle(
        components=[
            {"service_id": "netflix", "plan_id": "standard", "inclusion": "included"},
            {"service_id": "spotify", "inclusion": "discount_pct", "value": "30"},
            {"service_id": "hbo_max", "inclusion": "discount_abs", "value": "45000"},
            {"service_id": "paramount_plus", "inclusion": "discount_abs", "value": "9000"},
            {"service_id": "vix", "inclusion": "free_months", "value": "3"},
        ]
    )
    p = price(b)
    assert p.standalone_sum_eur == Decimal("5.98")  # Netflix only
    assert p.saving_pct == Decimal("-0.33")
    savings = {(o.service_id, o.inclusion): o for o in p.optional_savings}
    assert list(savings) == [
        ("spotify", "discount_pct"),
        ("hbo_max", "discount_abs"),
        ("paramount_plus", "discount_abs"),
        ("vix", "free_months"),
    ]
    assert savings["spotify", "discount_pct"].saving_month_eur == Decimal("1.13")  # 30 % of 3.76
    # 45000 COP = 10 EUR, capped at what HBO Max costs on its own (4.00).
    assert savings["hbo_max", "discount_abs"].saving_month_eur == Decimal("4.00")
    # No standalone price: the amount converted (9000 / 4500).
    assert savings["paramount_plus", "discount_abs"].saving_month_eur == Decimal("2.00")
    assert savings["vix", "free_months"].saving_month_eur is None
    assert savings["vix", "free_months"].value == Decimal("3")


def test_no_fx_table_no_eur_figures() -> None:
    p = price_bundle(bundle(), CO_ROWS, None, residence_currency="SEK", fx_fee_pct=2, today=TODAY)
    assert p.regular.amount == Decimal("27000")
    assert (p.monthly_eur, p.standalone_sum_eur, p.saving_pct) == (None, None, None)
    assert p.standalone_missing == ("netflix", "spotify")


@pytest.mark.parametrize("bundle_id", ["meli_plus_co_total", "meli_plus_co_esencial"])
def test_meli_plus_saving_stays_honest(bundle_id: str) -> None:
    """The YAML price is never "fixed" here (only a verified anchor changes it, H10): the
    saving is computed from it as it is, and the 30 % discounts stay out of saving_pct."""
    b = next(x for x in load_bundles().bundles if x.bundle_id == bundle_id)
    rows = [offer("disney_plus", "standard_with_ads", "CO", "23900", "COP")]
    p = price(b, rows)
    assert p.regular.amount == b.price.amount
    if bundle_id == "meli_plus_co_total":
        assert b.price.amount == Decimal("24900")
        assert p.standalone_sum_eur == Decimal("5.31")
        assert p.saving_pct is not None and p.saving_pct < 0
    else:
        assert p.standalone_sum_eur is None and p.saving_pct is None  # nothing included
    assert all(o.inclusion == "discount_pct" for o in p.optional_savings)
