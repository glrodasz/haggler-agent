"""haggler.sources.heuristics: price roles, billing periods and plan/price pairing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from haggler.sources.base import market_segment
from haggler.sources.heuristics import (
    classify_prices,
    detect_period,
    is_comparison,
    is_intro,
    is_savings,
    period_marker,
    plan_price_pairs,
)


def _roles(text, expected, **kw):
    return [(str(i.money.amount), i.period, i.role) for i in classify_prices(text, expected, **kw)]


@pytest.mark.parametrize(
    ("text", "expected", "roles"),
    [
        # Period from the text right after each price, up to the next price.
        (
            "109 kr/mån eller 1 090 kr/år",
            ["SEK"],
            [("109", "month", "regular"), ("1090", "year", "regular")],
        ),
        (
            "$10.99/mo. or $109/yr.",
            ["USD"],
            [("10.99", "month", "regular"), ("109", "year", "regular")],
        ),
        ("Monthly: 129 kr", ["SEK"], [("129", "month", "regular")]),  # lone price: marker before
        ("1 290 kr per år", ["SEK"], [("1290", "year", "regular")]),
        ("69 kr per månad", ["SEK"], [("69", "month", "regular")]),
        ("€4,99 al mes", ["EUR"], [("4.99", "month", "regular")]),
        ("129 kr", ["SEK"], [("129", None, "regular")]),
        # Comparison / struck-through prices.
        ("Inte 339 kr/mån per månad", ["SEK"], [("339", "month", "compare")]),
        ("Not $29.95/mo.", ["USD"], [("29.95", "month", "compare")]),
        (
            "statt 9,99 € nur 4,99 €",
            ["EUR"],
            [("9.99", None, "compare"), ("4.99", None, "regular")],
        ),
        ("au lieu de 12,99 €", ["EUR"], [("12.99", None, "compare")]),
        ("en lugar de $ 19.900", ["COP"], [("19900", None, "compare")]),
        ("Antes $ 26.900", ["COP"], [("26900", None, "compare")]),
        ("Regular price $16.99/mo.", ["USD"], [("16.99", "month", "compare")]),
        (
            "$19.95/mo. — $27.95/mo. separately",
            ["USD"],
            [("19.95", "month", "regular"), ("27.95", "month", "compare")],
        ),
        # Savings amounts.
        ("Spara 124 kr/mån** på favoritinnehåll", ["SEK"], [("124", "month", "savings")]),
        ("Save over $8/mo.", ["USD"], [("8", "month", "savings")]),
        ("Ahorra hasta $ 10.000", ["COP"], [("10000", None, "savings")]),
        ("Économisez 2 € par mois", ["EUR"], [("2", "month", "savings")]),
        ("Sparen Sie 3 € im Monat", ["EUR"], [("3", None, "savings")]),
        ("a $8 savings every month", ["USD"], [("8", None, "savings")]),
        # "Spara stort för bara 69 kr" is the student price, not a saving.
        (
            "Spara stort för bara 69 kr/månad, första månaden är gratis när du tecknar",
            ["SEK"],
            [("69", "month", "regular")],
        ),
        # Intro offers on either side of the price; "then ..." marks the regular price.
        (
            "Get 3 months for $2.99/mo., then $16.99/mo.",
            ["USD"],
            [("2.99", "month", "intro"), ("16.99", "month", "regular")],
        ),
        (
            "$2.99/mo. for 3 months, then $12.99/mo.",
            ["USD"],
            [("2.99", "month", "intro"), ("12.99", "month", "regular")],
        ),
        (
            "0 kr i 1 månad, sedan 129 kr/månad",
            ["SEK"],
            [("0", None, "intro"), ("129", "month", "regular")],
        ),
        ("3 meses por COP 9.250", ["COP"], [("9250", None, "intro")]),
        # A free first month mentioned after the price does not make it an intro price.
        (
            "119 kr/månad, första månaden är gratis när du tecknar ett nytt abonnemang.",
            ["SEK"],
            [("119", "month", "regular")],
        ),
        ("209 kr/månad, en månad gratis", ["SEK"], [("209", "month", "regular")]),
    ],
)
def test_classify_prices(text, expected, roles):
    assert _roles(text, expected, intro_words=True) == roles


def test_period_split_off_on_the_next_line():
    assert _roles("1 090 kr", ["SEK"], next_text="/år") == [("1090", "year", "regular")]
    # A long next line is another block, not this price's period.
    assert _roles("1 090 kr", ["SEK"], next_text="Avsluta när du vill. 129 kr/mån")[0][1] is None


def test_intro_words_are_opt_in():
    text = "Erbjudande: 59 kr/mån"
    assert _roles(text, ["SEK"])[0][2] == "regular"
    assert _roles(text, ["SEK"], intro_words=True)[0][2] == "intro"


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("/mo. or $109/yr.", "month"),  # earliest marker wins, not year-first
        ("/år, or 109 kr/mån", "year"),
        (" per quarter", "quarter"),
        ("/kk", "month"),
        ("/v", "year"),
        (" i månaden", "month"),
        (" om året", "year"),
        ("", None),
        (" incl. VAT", None),
    ],
)
def test_period_marker(text, period):
    assert period_marker(text) == period
    assert detect_period(text) == period


@pytest.mark.parametrize(
    ("before", "after", "intro"),
    [
        ("Free for 1 month, then ", "/mo.", False),
        ("", "/mo. for 3 months", True),
        ("", "/mo., for 3 months", True),
        ("Get 3 months of Apple TV for ", "/mo.", True),
        ("Then ", "/mo. for 12 months", False),  # "then" right before wins
        ("", " i 1 månad", True),
        ("", "/månad efteråt", False),
    ],
)
def test_is_intro(before, after, intro):
    assert is_intro(before, after) is intro


def test_comparison_and_savings_need_the_marker_next_to_the_price():
    assert is_comparison("Inte ", "/mån")
    assert not is_comparison("Det kostar inte mycket: ", "/mån")
    assert is_savings("Spara ", "/mån")
    assert is_savings("Save up to ", "")
    assert not is_savings("Spara stort för bara ", "/månad")


@pytest.fixture(scope="module")
def apple_music(catalog):
    return catalog.service("apple_music")


def _pairs(svc, lines, expected):
    return {
        pp.plan_id: (pp.money.amount, pp.period)
        for pp in plan_price_pairs(lines, svc, expected, window=8)
    }


def test_plan_price_pairs_review_scenario(apple_music):
    # python_correctness/F1: "X/mo. or Y/yr." was read as yearly and the intro price won.
    lines = [
        "Student",
        "$5.99/mo.",
        "Individual",
        "$10.99/mo. or $109/yr.",
        "Family",
        "Get 3 months for $2.99/mo., then $16.99/mo.",
    ]
    assert _pairs(apple_music, lines, ["USD"]) == {
        "student": (Decimal("5.99"), "month"),
        "individual": (Decimal("10.99"), "month"),
        "family": (Decimal("16.99"), "month"),
    }


def test_plan_price_pairs_swedish(apple_music):
    lines = ["Individuell", "109 kr/mån eller 1 090 kr/år", "Familj", "1 690 kr/år"]
    assert _pairs(apple_music, lines, ["SEK"]) == {
        "individual": (Decimal("109"), "month"),
        "family": (Decimal("1690"), "year"),  # only a yearly price: kept with its period
    }


def test_plan_price_pairs_skips_compare_savings_and_intro_only(apple_music):
    lines = [
        "Personligt",
        "Inte 339 kr/mån",
        "Spara 124 kr/mån",
        "119 kr/mån",
        "Familj",
        "3 månader för 0 kr",
        "Prova 1 månad för 49 kr",
    ]
    # Family has only offers: no price rather than an intro price.
    assert _pairs(apple_music, lines, ["SEK"]) == {"individual": (Decimal("119"), "month")}


def test_market_segment():
    assert market_segment({}, "SE") == "se"
    assert market_segment({"path_overrides": {"GB": "uk"}}, "GB") == "uk"
    assert market_segment({"path_overrides": {"GB": "/UK/"}}, "GB") == "uk"
    assert market_segment({"path_overrides": {"GB": "uk"}}, "SE") == "se"
    assert market_segment({"path_overrides": None}, "SE") == "se"
