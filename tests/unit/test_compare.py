"""Cheapest feasible offer per plan, with the real catalog and curated feasibility seeds."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from haggler.compare import RISK_LEVELS, cheapest
from haggler.feasibility import UNKNOWN_RANK
from haggler.models.curated import FeasibilityFile, FeasibilityRecord
from haggler.models.fx import FxRate, FxTable
from haggler.models.observations import PERIOD_MONTHS, PublishedPrice, obs_key

D = date(2026, 10, 8)
RATES = {"SEK": "11", "COP": "4500", "USD": "1.10", "TRY": "48", "MXN": "20", "NOK": "11.5"}
FX = FxTable(
    as_of=D,
    rates={
        cur: FxRate(currency=cur, per_eur=Decimal(v), source="ecb", as_of=D)
        for cur, v in RATES.items()
    },
)


def offer(
    service: str,
    plan: str,
    cc: str,
    amount: str,
    currency: str,
    *,
    channel: str = "web",
    period: str = "month",
    kind: str = "official",
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
        source_url=f"https://example.test/{service}/{cc.lower()}",
        source_kind=kind,
        adapter_id="test",
        extraction="parser",
        confidence=0.9,
        status=status,
        first_seen=D,
        last_changed=D,
    )


def run(offers, catalog, feasibility, **kw):
    rows = cheapest(offers, fx=FX, catalog=catalog, feasibility=feasibility, **kw)
    return {(r.service_id, r.plan_id): r for r in rows}


def countries(row):
    return {k: (b.country if b else None) for k, b in row.best_by_risk.items()}


NETFLIX = [
    offer("netflix", "standard", "SE", "169", "SEK"),  # home: 15.36
    offer("netflix", "standard", "CO", "38900", "COP"),  # 8.64 -> 8.82 with fee, rank 2
    offer("netflix", "standard", "TR", "149.99", "TRY"),  # 3.12 -> 3.19, no record: unknown
    offer("netflix", "standard", "US", "17.99", "USD"),  # 16.35 -> 16.68, unknown
]


def test_netflix_risk_thresholds(catalog, feasibility):
    row = run(NETFLIX, catalog, feasibility)["netflix", "standard"]
    assert row.home.country == "SE"
    assert (row.home.cost_eur, row.home.cost_eur_with_fee) == (Decimal("15.36"), Decimal("15.36"))
    assert row.home.label == "official_home" and row.home.rank == 0
    assert countries(row) == {0: "SE", 1: "SE", 2: "CO", 3: "CO"}
    co = row.best_by_risk[2]
    assert (co.cost_eur, co.cost_eur_with_fee) == (Decimal("8.64"), Decimal("8.82"))
    assert (co.label, co.rank, co.eu_badge) == ("tos_breach_low_enforcement", 2, False)
    assert row.savings_month_eur == {
        0: Decimal("0.00"),
        1: Decimal("0.00"),
        2: Decimal("6.54"),
        3: Decimal("6.54"),
    }


def test_unknown_offers_are_listed_but_never_best(catalog, feasibility):
    row = run(NETFLIX, catalog, feasibility)["netflix", "standard"]
    assert [o.country for o in row.all_offers] == ["TR", "CO", "SE", "US"]
    tr = row.all_offers[0]
    assert (tr.label, tr.rank) == ("unknown", UNKNOWN_RANK)
    assert tr.cost_eur_with_fee == Decimal("3.19")
    assert all(b.country != "TR" for b in row.best_by_risk.values())


def test_not_feasible_and_enforced_offers(catalog, feasibility):
    offers = [
        offer("youtube_premium", "individual", "SE", "159", "SEK", channel="ios_iap"),
        offer("youtube_premium", "individual", "CO", "26900", "COP", channel="ios_iap"),
        # Cheapest of all, but youtube_premium US is "not_feasible" for this profile.
        offer("youtube_premium", "individual", "US", "1.99", "USD", channel="ios_iap"),
    ]
    row = run(offers, catalog, feasibility)["youtube_premium", "individual"]
    assert row.home.channel == "ios_iap"  # no web row at home
    assert row.home.cost_eur == Decimal("14.45")
    assert countries(row) == {0: "SE", 1: "SE", 2: "SE", 3: "CO"}
    assert row.best_by_risk[3].label == "tos_breach_enforced"
    assert row.savings_month_eur[2] == Decimal("0.00")
    assert row.savings_month_eur[3] == Decimal("14.45") - Decimal("6.10")
    us = next(o for o in row.all_offers if o.country == "US")
    assert (us.label, us.rank) == ("not_feasible", 4)


def test_fx_fee_can_flip_the_winner(catalog, feasibility):
    offers = [
        offer("icloud_plus", "200gb", "SE", "33", "SEK"),  # 3.00, no fee at home
        offer("icloud_plus", "200gb", "LT", "2.99", "EUR"),  # 2.99 -> 3.05 with the 2% fee
    ]
    row = run(offers, catalog, feasibility)["icloud_plus", "200gb"]
    lt = next(o for o in row.all_offers if o.country == "LT")
    assert (lt.cost_eur, lt.cost_eur_with_fee, lt.eu_badge) == (
        Decimal("2.99"),
        Decimal("3.05"),
        True,
    )
    assert countries(row)[2] == "SE"
    assert row.savings_month_eur[2] == Decimal("0.00")

    no_fee = run(offers, catalog, feasibility, fx_fee_pct=0.0)["icloud_plus", "200gb"]
    assert countries(no_fee)[2] == "LT"
    assert no_fee.savings_month_eur[2] == Decimal("0.01")


@pytest.mark.parametrize(("fee", "expected"), [(0.0, "8.64"), (2.0, "8.82"), (5.0, "9.08")])
def test_fee_percentage(catalog, feasibility, fee, expected):
    row = run(NETFLIX, catalog, feasibility, fx_fee_pct=fee)["netflix", "standard"]
    assert row.best_by_risk[2].cost_eur_with_fee == Decimal(expected)
    # Never on offers in the residence currency.
    assert row.home.cost_eur_with_fee == row.home.cost_eur


def test_home_prefers_web_over_cheaper_ios_iap(catalog, feasibility):
    offers = [
        offer("spotify", "individual", "SE", "159", "SEK", channel="ios_iap"),
        offer("spotify", "individual", "SE", "179", "SEK"),
    ]
    row = run(offers, catalog, feasibility)["spotify", "individual"]
    assert row.home.channel == "web"
    # The cheaper in-app row still wins the comparison.
    assert row.best_by_risk[0].channel == "ios_iap"
    assert row.savings_month_eur[0] == Decimal("16.27") - Decimal("14.45")


def test_no_home_offer(catalog, feasibility):
    offers = [offer("apple_tv", "standard", "CO", "16900", "COP")]
    row = run(offers, catalog, feasibility)["apple_tv", "standard"]
    assert row.home is None
    assert countries(row) == {0: None, 1: None, 2: "CO", 3: "CO"}
    assert set(row.savings_month_eur.values()) == {None}


def test_only_accepted_and_carried_forward_rows_compete(catalog, feasibility):
    offers = [
        offer("netflix", "standard", "SE", "169", "SEK"),
        offer("netflix", "standard", "CO", "38900", "COP", status="carried_forward"),
        offer("netflix", "standard", "MX", "10", "MXN", status="rejected"),
        offer("netflix", "standard", "NO", "10", "NOK", status="quarantined"),
    ]
    row = run(offers, catalog, feasibility)["netflix", "standard"]
    assert [o.country for o in row.all_offers] == ["CO", "SE"]
    assert row.best_by_risk[2].country == "CO"


def test_offers_without_fx_are_skipped(catalog, feasibility):
    offers = [
        offer("netflix", "standard", "SE", "169", "SEK"),
        offer("netflix", "standard", "BR", "44.90", "BRL"),  # no BRL rate in FX
        offer("netflix", "premium", "BR", "59.90", "BRL"),
    ]
    rows = run(offers, catalog, feasibility)
    assert [o.country for o in rows["netflix", "standard"].all_offers] == ["SE"]
    assert ("netflix", "premium") not in rows


def _feas_netflix(*targets: str) -> FeasibilityFile:
    return FeasibilityFile(
        profiles={"se_resident_co_citizen": "test"},
        records=[
            FeasibilityRecord(
                id=f"netflix_{cc.lower()}",
                service_id="netflix",
                target_country=cc,
                residence_country="SE",
                profile="se_resident_co_citizen",
                legality="lawful",
                tos="breach_unenforced",
                practical="remote",
                last_verified=D,
            )
            for cc in targets
        ],
    )


def test_tie_breaks(catalog):
    feas = _feas_netflix("CO", "MX")
    # All cost 9.00 EUR (9.18 with fee).
    co_web = offer("netflix", "standard", "CO", "40500", "COP")
    co_iap = offer("netflix", "standard", "CO", "40500", "COP", channel="ios_iap")
    mx_web = offer("netflix", "standard", "MX", "180", "MXN")
    co_web_secondary = offer("netflix", "standard", "CO", "40500", "COP", kind="secondary")

    def best(*offers):
        return run(list(offers), catalog, feas)["netflix", "standard"].best_by_risk[2]

    assert best(co_iap, mx_web).country == "MX"  # web beats ios_iap
    assert best(co_iap, co_web).channel == "web"
    assert best(mx_web, co_web_secondary).country == "MX"  # official beats secondary
    assert best(mx_web, co_web).country == "CO"  # then the lower country code
    # Input order never matters.
    assert best(co_web, mx_web, co_iap) == best(co_iap, mx_web, co_web)


def test_sub_cent_differences_are_ties(catalog):
    feas = _feas_netflix("CO", "MX")
    # 9.0022 EUR vs 9.0000 EUR: same cent after the fee, so the country code decides.
    mx = offer("netflix", "standard", "MX", "180.00", "MXN")
    co = offer("netflix", "standard", "CO", "40510", "COP")
    row = run([mx, co], catalog, feas)["netflix", "standard"]
    assert row.best_by_risk[2].country == "CO"


def test_rows_sorted_by_service_then_plan_rank(catalog, feasibility):
    offers = [
        offer("spotify", "family", "SE", "289", "SEK"),
        offer("netflix", "premium", "SE", "229", "SEK"),
        offer("netflix", "basic", "SE", "129", "SEK"),
        offer("netflix", "basic_with_ads", "SE", "69", "SEK"),
        offer("netflix", "mobile", "SE", "59", "SEK"),
        offer("icloud_plus", "2tb", "SE", "129", "SEK"),
        offer("icloud_plus", "50gb", "SE", "12", "SEK"),
        offer("spotify", "student", "SE", "75", "SEK"),
    ]
    rows = cheapest(offers, fx=FX, catalog=catalog, feasibility=feasibility)
    assert [(r.service_id, r.plan_id) for r in rows] == [
        ("icloud_plus", "50gb"),
        ("icloud_plus", "2tb"),
        ("netflix", "mobile"),
        ("netflix", "basic_with_ads"),  # same rank as basic: catalog order
        ("netflix", "basic"),
        ("netflix", "premium"),
        ("spotify", "student"),
        ("spotify", "family"),
    ]
    for row in rows:
        assert set(row.best_by_risk) == set(RISK_LEVELS) == set(row.savings_month_eur)


def test_money_is_quantized_decimal(catalog, feasibility):
    for row in cheapest(NETFLIX, fx=FX, catalog=catalog, feasibility=feasibility):
        for ref in row.all_offers:
            for value in (ref.cost_eur, ref.cost_eur_with_fee):
                assert isinstance(value, Decimal)
                assert value.as_tuple().exponent == -2
        for saving in row.savings_month_eur.values():
            assert saving is None or saving.as_tuple().exponent == -2


def test_other_residence(catalog, feasibility):
    row = run(NETFLIX, catalog, feasibility, residence="CO")["netflix", "standard"]
    assert row.home.country == "CO"
    assert row.home.cost_eur_with_fee == Decimal("8.64")  # COP is the home currency now
    se = next(o for o in row.all_offers if o.country == "SE")
    assert se.label == "unknown"  # curated records are for residence SE
    assert se.cost_eur_with_fee == Decimal("15.67")
    assert countries(row) == {0: "CO", 1: "CO", 2: "CO", 3: "CO"}


def test_unknown_residence_is_an_error(catalog, feasibility):
    with pytest.raises(ValueError, match="XX"):
        cheapest(NETFLIX, fx=FX, catalog=catalog, feasibility=feasibility, residence="XX")
