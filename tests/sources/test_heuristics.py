"""haggler.sources.heuristics: price roles, billing periods and plan/price pairing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from haggler.sources.base import market_segment
from haggler.sources.heuristics import (
    classify_prices,
    detect_period,
    follows_then,
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


# prices/F3: label-first layouts ("Monthly: X · Annual: Y", or one label per line).
@pytest.mark.parametrize(
    ("text", "expected", "periods"),
    [
        ("Monthly: $10.99 · Annual: $109", ["USD"], [("10.99", "month"), ("109", "year")]),
        ("Annual: $109 · Monthly: $10.99", ["USD"], [("109", "year"), ("10.99", "month")]),
        ("Aylık: ₺39,99 · Yıllık: ₺399", ["TRY"], [("39.99", "month"), ("399", "year")]),
        ("Månadsvis: 129 kr, Årsvis: 1 290 kr", ["SEK"], [("129", "month"), ("1290", "year")]),
        ("Årsvis: 1 290 kr", ["SEK"], [("1290", "year")]),
        # Price-first suffix markers are unchanged.
        ("$10.99/mo. · Annual: $109", ["USD"], [("10.99", "month"), ("109", "year")]),
        ("Annual plan: $9.99/mo. billed yearly", ["USD"], [("9.99", "month")]),
    ],
)
def test_label_first_periods(text, expected, periods):
    assert [(a, p) for a, p, _ in _roles(text, expected)] == periods


def test_next_line_with_its_own_price_does_not_lend_its_period():
    assert _roles("Annual: $109", ["USD"], next_text="Monthly: $10.99")[0][1] == "year"
    assert _roles("Monthly: $10.99", ["USD"], next_text="Annual: $109")[0][1] == "month"
    assert _roles("$10.99", ["USD"], next_text="Annual: $109")[0][1] is None
    # A label line ("Annual:" above its own price) belongs to the next price, not this one.
    assert _roles("$10.99", ["USD"], next_text="Annual:")[0][1] is None
    assert _roles("1 090 kr", ["SEK"], next_text="/år")[0][1] == "year"


@pytest.mark.parametrize(
    "lines",
    [
        ["Individual", "Monthly: $10.99 · Annual: $109"],
        ["Individual", "Annual: $109", "Monthly: $10.99"],
        ["Individual", "Monthly: $10.99", "Annual: $109"],
        ["Individual", "$10.99", "Annual:", "$109"],  # the label line is not borrowed
    ],
)
def test_plan_price_pairs_label_first(apple_music, lines):
    assert _pairs(apple_music, lines, ["USD"]) == {"individual": (Decimal("10.99"), "month")}


# prices/F4: "Try" is not a TRY price, so "Try 3 months for $2.99/mo." stays an intro offer.
def test_try_n_months_is_an_intro_offer():
    assert _roles("Try 3 months for $2.99/mo.", ["USD"]) == [("2.99", "month", "intro")]
    assert _roles("Try 1 month free, then $12.99/mo.", ["TRY", "USD"]) == [
        ("12.99", "month", "regular")
    ]


def test_intro_count_swallowed_by_an_earlier_price_still_counts():
    # "Le 3" reads as an EGP amount on an Egyptian page; the offer "3 mois pour" is still
    # seen in the whole line before the next price.
    roles = _roles("Le 3 mois pour 49 E£, puis 99 E£ par mois", ["EGP"])
    assert roles[-2:] == [("49", None, "intro"), ("99", "month", "regular")]
    # The offer-word rule stays limited to the text since the previous price.
    roles = _roles("Special offer $2.99 for 3 months, $12.99/mo. after", ["USD"], intro_words=True)
    assert roles == [("2.99", None, "intro"), ("12.99", "month", "regular")]


def test_suffix_marker_followed_by_a_colon_is_still_the_prices_own():
    assert _roles("$10.99 per month: cancel anytime", ["USD"]) == [("10.99", "month", "regular")]
    assert _roles("€99 yearly: best value", ["EUR"]) == [("99", "year", "regular")]


@pytest.mark.parametrize(
    ("before", "then"),
    [
        ("Apple One Familj: 0 kr i 1 månad, sedan ", True),
        ("$ 9.900/mes por 3 meses, luego ", True),
        ("Free for 1 month, then just ", True),
        ("Ordinarie pris: ", False),
        ("Meli+ Total: ", False),
    ],
)
def test_follows_then(before: str, then: bool) -> None:
    assert follows_then(before) is then


def test_classify_prices_reads_currency_words_only_when_asked() -> None:
    line = "19900 pesos por mes, por 2 meses. Precio anterior: $ 21.900 pesos por mes."
    assert [(str(i.money.amount), i.role) for i in classify_prices(line, ["COP"])] == [
        ("21900", "regular")
    ]
    infos = classify_prices(line, ["COP"], currency_words=True)
    assert [(str(i.money.amount), i.period, i.role) for i in infos] == [
        ("19900", "month", "intro"),
        ("21900", "month", "regular"),
    ]
