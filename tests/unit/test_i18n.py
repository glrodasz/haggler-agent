import pickle
from datetime import date
from decimal import Decimal

from haggler.i18n import Text, concat, fill, join_list, localized, long_date, money, sentence, tr


def test_text_is_its_spanish_str() -> None:
    t = tr("hola", "hello", "olá")
    assert t == "hola" and isinstance(t, str)
    assert (t.en, t.pt) == ("hello", "olá")
    assert list(dict.fromkeys([t, tr("hola", "hello", "olá")])) == ["hola"]
    assert pickle.loads(pickle.dumps(t)).en == "hello"


def test_localized_falls_back_to_spanish_for_plain_str() -> None:
    assert localized(tr("a", "b", "c"), "pt") == "c"
    assert localized("solo español", "en") == "solo español"


def test_sentence_capitalizes_each_locale() -> None:
    s = sentence(tr("se asume x", "assumes x", "supõe-se x"))
    assert isinstance(s, Text)
    assert (s, s.en, s.pt) == ("Se asume x.", "Assumes x.", "Supõe-se x.")


def test_fill_concat_and_lists() -> None:
    t = fill(
        tr("{a} en {h}", "{a} on {h}", "{a} em {h}"), a=tr("precio", "price", "preço"), h="x.com"
    )
    assert (t, t.en, t.pt) == ("precio en x.com", "price on x.com", "preço em x.com")
    c = concat(tr("a: ", "b: ", "c: "), "z")
    assert (c, c.en, c.pt) == ("a: z", "b: z", "c: z")
    j = join_list([tr("uno", "one", "um"), tr("dos", "two", "dois")])
    assert (j, j.en, j.pt) == ("uno y dos", "one and two", "um e dois")


def test_dates_and_money_follow_each_locale() -> None:
    d = long_date(date(2026, 10, 3))
    assert (d, d.en, d.pt) == ("3 de octubre de 2026", "October 3, 2026", "3 de outubro de 2026")
    m = money(Decimal("21900"), "COP")
    assert (m, m.en, m.pt) == ("21.900 COP", "21,900 COP", "21.900 COP")
    m = money(Decimal("13.99"), "EUR")
    assert (m, m.en) == ("13,99 EUR", "13.99 EUR")
