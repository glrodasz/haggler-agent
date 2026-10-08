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
