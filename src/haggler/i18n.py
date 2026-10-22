"""Sentences the site shows in its three languages (es, en, pt).

The evaluators and the bundle state write their reasons and assumptions as `Text`: a str whose
value is the Spanish sentence (what the CLI, the tests and the dedupe by dict.fromkeys read)
carrying its English and Portuguese versions. publish.py writes `<field>_es/_en/_pt` from it.
Plain str operations (concatenation, f-strings) return a plain Spanish str, so every sentence
is built with `tr` or `Text.map`; `localized` falls back to the Spanish text for a plain str.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

Locale = Literal["es", "en", "pt"]
LOCALES: tuple[Locale, ...] = ("es", "en", "pt")


class Text(str):
    """A Spanish sentence (its str value) with its English (`en`) and Portuguese (`pt`)."""

    en: str
    pt: str

    def __new__(cls, es: str, en: str, pt: str) -> Text:
        obj = super().__new__(cls, es)
        obj.en = en
        obj.pt = pt
        return obj

    def __getnewargs__(self) -> tuple[str, str, str]:  # type: ignore[override]
        return (str(self), self.en, self.pt)

    def __repr__(self) -> str:
        return f"Text({str(self)!r}, en={self.en!r}, pt={self.pt!r})"

    def get(self, locale: Locale) -> str:
        return {"es": str(self), "en": self.en, "pt": self.pt}[locale]

    def map(self, f: Callable[[str], str]) -> Text:
        return Text(f(str(self)), f(self.en), f(self.pt))


def tr(es: str, en: str, pt: str) -> Text:
    return Text(es, en, pt)


def localized(text: str, locale: Locale) -> str:
    """`text` in `locale`; a plain str has only its Spanish version."""
    return text.get(locale) if isinstance(text, Text) else text


def as_text(text: str) -> Text:
    """A Text as is; a plain str (e.g. a curated reason without translations) in every locale."""
    return text if isinstance(text, Text) else Text(text, text, text)


def concat(*parts: str) -> Text:
    """The parts joined per locale (each part a Text or language-neutral str, e.g. a host)."""
    return Text(*("".join(localized(p, loc) for p in parts) for loc in LOCALES))


def join_list(items: Sequence[str]) -> Text:
    """'a, b y c' / 'a, b and c' / 'a, b e c'."""
    words = {"es": "y", "en": "and", "pt": "e"}

    def one(loc: Locale) -> str:
        texts = [localized(i, loc) for i in items]
        if len(texts) == 1:
            return texts[0]
        return f"{', '.join(texts[:-1])} {words[loc]} {texts[-1]}"

    return Text(*(one(loc) for loc in LOCALES))


def join(sep: str, items: Iterable[str]) -> Text:
    items = list(items)
    return Text(*(sep.join(localized(i, loc) for i in items) for loc in LOCALES))


_MONTHS = {
    "es": (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "pt": (
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
}


def long_date(d: date) -> Text:
    """'31 de agosto de 2026' / 'August 31, 2026' / '31 de agosto de 2026', like the site."""
    m = d.month - 1
    return Text(
        f"{d.day} de {_MONTHS['es'][m]} de {d.year}",
        f"{_MONTHS['en'][m]} {d.day}, {d.year}",
        f"{d.day} de {_MONTHS['pt'][m]} de {d.year}",
    )


def money(amount: Decimal, currency: str) -> Text:
    """'21.900 COP' / '21,900 COP' / '21.900 COP'; '13,99 EUR' / '13.99 EUR' / '13,99 EUR'."""
    en = f"{int(amount):,}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    # Spanish and Portuguese swap the separators.
    es = en.replace(",", " ").replace(".", ",").replace(" ", ".")
    return Text(f"{es} {currency}", f"{en} {currency}", f"{es} {currency}")


def sentence(text: str) -> str:
    """Whitespace collapsed, first letter upper case, a final period (per locale for a Text)."""

    def one(s: str) -> str:
        s = " ".join(s.split())
        if not s:
            return s
        s = s[0].upper() + s[1:]
        return s if s[-1] in ".!?" else s + "."

    return text.map(one) if isinstance(text, Text) else one(text)


def fill(template: Text, **values: str) -> Text:
    """str.format per locale, each value in that locale (a Text or a language-neutral str)."""
    return Text(
        *(
            template.get(loc).format(**{k: localized(v, loc) for k, v in values.items()})
            for loc in LOCALES
        )
    )
