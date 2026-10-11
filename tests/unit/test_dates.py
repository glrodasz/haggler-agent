"""haggler.dates.find_dates: dates written in es / pt / sv / en, the year required."""

from __future__ import annotations

from datetime import date

import pytest

from haggler.dates import contains_date, date_direction, find_dates

DEC_31 = date(2026, 12, 31)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # es
        ("Oferta válida hasta el 31 de diciembre de 2026.", DEC_31),
        ("Vigente hasta el 1° de enero del 2027", date(2027, 1, 1)),
        ("Promoción válida hasta diciembre 31 de 2026", DEC_31),
        ("Aplica hasta el 31-dic-2026", DEC_31),
        ("Termina el 5 de dic. de 2026", date(2026, 12, 5)),
        ("Vigente hasta el 1ro de enero de 2027", date(2027, 1, 1)),  # Latin American ordinal
        ("Vigente hasta el 1ro. de enero de 2027", date(2027, 1, 1)),
        ("Vence 1.° de marzo de 2027", date(2027, 3, 1)),
        ("Desde el 2do de marzo de 2027", date(2027, 3, 2)),
        # pt
        ("Oferta válida até 31 de dezembro de 2026", DEC_31),
        ("válido até 31 DE MARÇO DE 2027", date(2027, 3, 31)),
        ("Promoção até 1º de setembro de 2026", date(2026, 9, 1)),
        # sv
        ("Erbjudandet gäller fr.o.m. 1 april 2026", date(2026, 4, 1)),
        ("Rabatten gäller t.o.m. 31 mars 2027", date(2027, 3, 31)),
        ("Gäller till 30 sep. 2026", date(2026, 9, 30)),
        ("Från den 1:a april 2026", date(2026, 4, 1)),
        # en
        ("Offer ends 31 December 2026", DEC_31),
        ("Valid until December 31, 2026", DEC_31),
        ("From 1st April, 2026", date(2026, 4, 1)),
        ("valid until Sept. 30, 2026", date(2026, 9, 30)),
        # numeric
        ("Hasta el 31/12/2026", DEC_31),
        ("Giltig till 2026-12-31", DEC_31),
        ("Válido até 31.12.2026", DEC_31),
        ("Valid through 12/31/2026", DEC_31),  # day-first is impossible, so month-first
    ],
)
def test_finds_the_date(text: str, expected: date) -> None:
    assert [d.value for d in find_dates(text)] == [expected]
    assert contains_date(text, expected)


@pytest.mark.parametrize(
    "text",
    [
        "Oferta válida hasta el 31 de diciembre",
        "Erbjudandet gäller fr.o.m. 1 april",
        "Hasta el 31/12",
        "Hasta el 31/12/26",  # a two-digit year would be a guess
        "La promo de mayo 2026",  # no day
        "El mayor ahorro de 2026",
        "$ 19.900/mes por 2 meses",
        "Precio anterior: $ 21.900 pesos por mes.",
        "31 de febrero de 2026",  # not a date
    ],
)
def test_no_date_without_day_month_and_year(text: str) -> None:
    assert find_dates(text) == []


def test_several_dates_in_reading_order_with_offsets() -> None:
    text = "Del 1 de octubre de 2026 al 31/12/2026 (renovación 2027-01-15)."
    found = find_dates(text)
    assert [d.value for d in found] == [date(2026, 10, 1), DEC_31, date(2027, 1, 15)]
    assert [d.raw for d in found] == ["1 de octubre de 2026", "31/12/2026", "2027-01-15"]
    assert all(text[d.start : d.end] == d.raw for d in found)


def test_accents_and_case_do_not_matter_and_offsets_stay_valid() -> None:
    text = "ATÉ 31 DE DEZEMBRO DE 2026 e até 1 de março de 2027"
    found = find_dates(text)
    assert [d.value for d in found] == [DEC_31, date(2027, 3, 1)]
    assert found[0].raw == "31 DE DEZEMBRO DE 2026"
    assert found[1].raw == "1 de março de 2027"


def test_whitespace_runs_count_as_one_space_and_offsets_stay_valid() -> None:
    text = "Hasta el 31  de\n diciembre   de 2026."
    (found,) = find_dates(text)
    assert found.value == DEC_31 and text[found.start : found.end] == found.raw
    assert found.raw == "31  de\n diciembre   de 2026"


def test_a_long_whitespace_run_after_a_month_is_linear() -> None:
    import time

    started = time.perf_counter()
    assert find_dates("1 dic" + " " * 50_000 + "x") == []
    assert time.perf_counter() - started < 1


@pytest.mark.parametrize(
    ("text", "direction"),
    [
        ("Nuevos precios a partir del 1 de noviembre de 2026", "from"),
        ("Erbjudandet gäller fr.o.m. 1 april 2026", "from"),
        ("Vigente desde el 4 de septiembre de 2026", "from"),
        ("Från den 1:a april 2026", "from"),
        ("Oferta válida hasta el 31 de diciembre de 2026", "until"),
        ("Oferta válida até 31 de dezembro de 2026", "until"),
        ("Rabatten gäller t.o.m. 31 mars 2027", "until"),
        ("Valid until December 31, 2026", "until"),
        ("Precio especial 31/12/2026", None),
    ],
)
def test_date_direction(text: str, direction: str | None) -> None:
    (found,) = find_dates(text)
    assert date_direction(text, found) == direction
