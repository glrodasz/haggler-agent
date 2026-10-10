import time
from decimal import Decimal

import pytest

from haggler.money import find_prices, parse_bare_amount, parse_money, parse_number

CASES = [
    ("129 kr/mån", ["SEK"], "129", "SEK"),
    ("129,00 kr", ["SEK"], "129.00", "SEK"),
    ("129:-", ["SEK"], "129", "SEK"),
    ("1 559 kr", ["SEK"], "1559", "SEK"),
    ("1 559 kr", ["SEK"], "1559", "SEK"),
    ("99 kr", ["NOK"], "99", "NOK"),
    ("79 kr.", ["DKK"], "79", "DKK"),
    ("€8.99", ["EUR"], "8.99", "EUR"),
    ("8,99 €", ["EUR"], "8.99", "EUR"),
    ("1.234,56 €", ["EUR"], "1234.56", "EUR"),
    ("EUR 13.99", ["EUR"], "13.99", "EUR"),
    ("$19.99", ["USD"], "19.99", "USD"),
    ("US$ 4.99", ["USD"], "4.99", "USD"),
    ("USD 15.99", ["USD"], "15.99", "USD"),
    ("CA$11.99", ["CAD"], "11.99", "CAD"),
    ("$18.900 / mes", ["COP"], "18900", "COP"),
    ("COP 18.900", ["COP"], "18900", "COP"),
    ("COP 18,900", ["COP"], "18900", "COP"),
    ("$ 44.900", ["COP"], "44900", "COP"),
    ("$149", ["MXN"], "149", "MXN"),
    ("$ 8.999", ["ARS"], "8999", "ARS"),
    ("$6.990", ["CLP"], "6990", "CLP"),
    ("R$ 34,90", ["BRL"], "34.90", "BRL"),
    ("S/ 24.90", ["PEN"], "24.90", "PEN"),
    ("£4.99", ["GBP"], "4.99", "GBP"),
    ("CHF 13.90", ["CHF"], "13.90", "CHF"),
    ("39,99 zł", ["PLN"], "39.99", "PLN"),
    ("199 Kč", ["CZK"], "199", "CZK"),
    ("2 990 Ft", ["HUF"], "2990", "HUF"),
    ("49,99 lei", ["RON"], "49.99", "RON"),
    ("₺289.99", ["TRY"], "289.99", "TRY"),
    ("189,99 TL", ["TRY"], "189.99", "TRY"),
    ("₹149/month", ["INR"], "149", "INR"),
    ("Rs 479", ["PKR"], "479", "PKR"),
    ("Rs. 899", ["PKR"], "899", "PKR"),
    ("E£ 100", ["EGP"], "100", "EGP"),
    ("₦ 2,200.00", ["NGN"], "2200.00", "NGN"),
    ("₦1,300", ["NGN"], "1300", "NGN"),
    ("₱149", ["PHP"], "149", "PHP"),
    ("Rp 59.000", ["IDR"], "59000", "IDR"),
    ("79.000 ₫", ["VND"], "79000", "VND"),
    ("R89.99", ["ZAR"], "89.99", "ZAR"),
]


@pytest.mark.parametrize(("text", "expected", "amount", "currency"), CASES)
def test_parse_money(text, expected, amount, currency):
    money = parse_money(text, expected)
    assert money is not None, text
    assert money.amount == Decimal(amount)
    assert money.currency == currency


def test_multiple_prices_in_one_line():
    found = find_prices("Standard 169 kr Premium 219 kr", ["SEK"])
    assert [m.amount for m in found] == [Decimal(169), Decimal(219)]


def test_short_markers_need_expected_currency():
    assert find_prices("open 24 hours rs 5", ["SEK"]) == []
    assert find_prices("R 99", ["SEK"]) == []


def test_ambiguous_dollar_without_expected_has_no_currency():
    money = parse_money("$9.99")
    assert money is not None and money.currency is None


@pytest.mark.parametrize(
    ("token", "value"),
    [
        ("18.900", "18900"),
        ("1,559", "1559"),
        ("4.99", "4.99"),
        ("129,0", "129.0"),
        ("1.234.567", "1234567"),
        ("2,200.00", "2200.00"),
        ("1'000", "1000"),
    ],
)
def test_parse_number(token, value):
    assert parse_number(token) == Decimal(value)


@pytest.mark.parametrize("token", ["1.2345", "12.34.5", "abc", ""])
def test_parse_number_rejects_garbage(token):
    assert parse_number(token) is None


def test_parse_bare_amount():
    assert parse_bare_amount("129,00") == Decimal("129.00")


# prices/F4: ISO codes only count in upper case, so English words are not currencies.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Try 1 month free", ["TRY"]),
        ("Try 3 months of Premium", ["USD"]),
        ("Ron 5 years ago", ["RON"]),
        ("pen 2 refills", ["PEN"]),
        ("cad 3 drawings", ["CAD"]),
    ],
)
def test_lowercase_iso_code_words_are_not_markers(text, expected):
    assert find_prices(text, expected) == []


def test_try_before_a_count_is_not_a_price():
    found = find_prices("Try 3 months for $2.99/mo.", ["USD"])
    assert [(m.amount, m.currency) for m in found] == [(Decimal("2.99"), "USD")]


@pytest.mark.parametrize(
    ("text", "expected", "amount", "currency"),
    [
        ("₺39,99 TRY", ["TRY"], "39.99", "TRY"),
        ("TRY 39,99", ["TRY"], "39.99", "TRY"),
        ("39,99 TL", ["TRY"], "39.99", "TRY"),
        ("39,99 tl", ["TRY"], "39.99", "TRY"),
        ("PEN 24.90", ["PEN"], "24.90", "PEN"),
        ("29 kr", ["SEK"], "29", "SEK"),
        ("29 KR", ["SEK"], "29", "SEK"),
        ("39,99 ZŁ", ["PLN"], "39.99", "PLN"),
        ("49,99 LEI", ["RON"], "49.99", "RON"),
        ("5,99 eur", ["EUR"], "5.99", "EUR"),
    ],
)
def test_uppercase_codes_and_case_insensitive_words_still_parse(text, expected, amount, currency):
    money = parse_money(text, expected)
    assert money is not None, text
    assert (money.amount, money.currency) == (Decimal(amount), currency)


# prices/F5: long digit runs without a marker must not take quadratic time.
@pytest.mark.parametrize(
    "text",
    ["1" * 6000, "111." * 1500, "123 456 " * 750, "9," * 3000],
    ids=["digits", "dot-groups", "space-groups", "comma-decimals"],
)
def test_long_digit_runs_are_fast(text):
    start = time.perf_counter()
    assert find_prices(text, ["SEK"]) == []
    assert time.perf_counter() - start < 0.5


# prices/F6: Arabic-Indic / Persian digits and the Arabic decimal and thousands separators.
@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("٤٩٫٩٩ ج.م.", "49.99"),
        ("١٬٢٩٩ ج.م.", "1299"),
        ("EGP ٤٩٫٩٩", "49.99"),
        ("٤٩٫٩٩ EGP", "49.99"),
        ("٤٩ ج.م", "49"),
        ("۴۹٫۹۹ EGP", "49.99"),
        ("49.99 ج.م.", "49.99"),
    ],
)
def test_arabic_digits_and_separators(text, amount):
    (money,) = find_prices(text, ["EGP"])
    assert (money.amount, money.currency) == (Decimal(amount), "EGP")
    # Offsets and raw text still point into the original string.
    assert text[money.start : money.end] == money.raw


def test_arabic_digits_in_bare_amounts():
    assert parse_number("٤٩٫٩٩") == Decimal("49.99")
    assert parse_number("١٬٢٩٩") == Decimal("1299")
    assert parse_bare_amount("السعر ١٬٢٩٩٫٥٠") == Decimal("1299.50")


# ---- currency words (value-vs-quote checks only) ---------------------------------------------

MELI_LINE = (
    "Precio actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. "
    "Precio anterior: $ 21.900 pesos por mes."
)


def test_currency_words_are_opt_in():
    """The daily pipeline never reads "19900 pesos" as a price; value checks do."""
    assert [(m.amount, m.currency) for m in find_prices(MELI_LINE, ["COP"])] == [
        (Decimal("21900"), "COP")
    ]
    found = find_prices(MELI_LINE, ["COP"], words=True)
    assert [(m.amount, m.currency, m.raw) for m in found] == [
        (Decimal("19900"), "COP", "19900 pesos"),
        (Decimal("21900"), "COP", "$ 21.900"),  # "pesos" after "$ 21.900" adds nothing
    ]


@pytest.mark.parametrize(
    ("text", "expected", "found"),
    [
        ("19900 pesos por mes", ["MXN"], [("19900", "MXN")]),
        ("19900 pesos por mes", ["SEK"], []),  # only where the country expects a peso
        ("215 kronor i månaden", ["SEK"], [("215", "SEK")]),
        ("74,90 reais por mês", ["BRL"], [("74.90", "BRL")]),
        ("pesos 19900", ["COP"], []),  # the word comes after the amount
        ("19900 pesosx", ["COP"], []),
    ],
)
def test_currency_words(text, expected, found):
    got = find_prices(text, expected, words=True)
    assert [(str(m.amount), m.currency) for m in got] == found
