"""Locale-tolerant price parsing.

`find_prices` scans free text for amounts next to a currency marker and resolves ambiguous
markers ("kr", "$") using the currencies expected for the page's country.

Separator rules (no locale needed):
  * both "." and "," present -> the right-most one is the decimal separator;
  * one separator kind, repeated -> thousands ("1.234.567");
  * one separator, followed by exactly 3 digits -> thousands ("18.900", "1,559");
  * one separator, followed by 1-2 digits -> decimal ("129,00", "4.99");
  * spaces / NBSP / thin spaces / apostrophes between digit groups -> thousands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Marker -> candidate currencies. Matching is case-insensitive.
_MARKERS: dict[str, tuple[str, ...]] = {
    "€": ("EUR",),
    "eur": ("EUR",),
    "euro": ("EUR",),
    "euros": ("EUR",),
    "kr.": ("DKK", "SEK", "NOK", "ISK"),
    "kr": ("SEK", "NOK", "DKK", "ISK"),
    ":-": ("SEK",),
    "us$": ("USD",),
    "usd": ("USD",),
    "u$s": ("USD",),
    "ca$": ("CAD",),
    "c$": ("CAD",),
    "mx$": ("MXN",),
    "col$": ("COP",),
    "ar$": ("ARS",),
    "clp$": ("CLP",),
    "r$": ("BRL",),
    "$": ("USD", "MXN", "COP", "ARS", "CLP", "CAD", "AUD", "NZD", "SGD"),
    "£": ("GBP",),
    "₺": ("TRY",),
    "tl": ("TRY",),
    "₹": ("INR",),
    "rs.": ("INR", "PKR"),
    "rs": ("INR", "PKR"),
    "₨": ("PKR", "INR"),
    "₦": ("NGN",),
    "zł": ("PLN",),
    "zl": ("PLN",),
    "kč": ("CZK",),
    "ft": ("HUF",),
    "lei": ("RON",),
    "fr.": ("CHF",),
    "sfr": ("CHF",),
    "₱": ("PHP",),
    "rp": ("IDR",),
    "₫": ("VND",),
    "đ": ("VND",),
    "vnđ": ("VND",),
    "e£": ("EGP",),
    "le": ("EGP",),
    "ج.م": ("EGP",),
    "ج.م.": ("EGP",),
    "s/": ("PEN",),
    "s/.": ("PEN",),
    "¥": ("JPY", "CNY"),
    "r": ("ZAR",),
}

# ISO codes we recognise directly as markers (in addition to the symbol table).
_ISO_CODES = [
    "SEK",
    "NOK",
    "DKK",
    "ISK",
    "EUR",
    "USD",
    "GBP",
    "CHF",
    "PLN",
    "CZK",
    "HUF",
    "RON",
    "TRY",
    "COP",
    "MXN",
    "ARS",
    "BRL",
    "CLP",
    "PEN",
    "CAD",
    "INR",
    "PKR",
    "EGP",
    "NGN",
    "PHP",
    "IDR",
    "VND",
    "ZAR",
    "JPY",
    "CNY",
    "AUD",
    "NZD",
    "SGD",
    "UAH",
]

# Currencies whose prices are effectively always shown without minor units.
ZERO_DECIMAL = {"CLP", "COP", "VND", "IDR", "JPY", "KRW", "ISK", "HUF", "PKR", "UGX"}

_SPACES = "\u00a0\u202f\u2009\u2007 "
# A digit run, then thousands groups (any separator, exactly 3 digits not followed by another
# digit), then an optional decimal part. Spaces only ever act as thousands separators, so a
# footnote digit after a price ("$59.99 4") is not glued onto it. The number starts where a digit
# run starts and is atomic: a long run without a marker fails once instead of being retried
# from every digit with every shorter length (quadratic time on a few thousand digits).
_NUM = rf"(?<!\d)(?>\d+(?:[.,'’{_SPACES}]\d{{3}}(?!\d))*(?:[.,]\d+)?)"

# Arabic-Indic and Persian digits and the Arabic decimal/thousands separators ("٤٩٫٩٩ ج.م.",
# "١٬٢٩٩") as ASCII. One character for one, so match offsets stay valid in the original text.
_DIGIT_TABLE = str.maketrans(
    {
        **{chr(0x0660 + i): str(i) for i in range(10)},
        **{chr(0x06F0 + i): str(i) for i in range(10)},
        "\u066b": ".",
        "\u066c": ",",
    }
)


def _ascii_digits(text: str) -> str:
    return text.translate(_DIGIT_TABLE)


def _marker_regex() -> re.Pattern[str]:
    tokens = sorted({*_MARKERS, *(c.lower() for c in _ISO_CODES)}, key=len, reverse=True)
    alts = []
    for t in tokens:
        esc = re.escape(t)
        if t not in _MARKERS:
            # Bare ISO codes only count in upper case: "Try 3 months" is not 3 TRY, nor
            # "pen"/"cad"/"Ron" a currency. Words and symbols ("kr", "zł", "eur") stay caseless.
            esc = rf"(?-i:{re.escape(t.upper())})"
        # Alphabetic markers must not be glued to other letters ("rs" inside "hours").
        if t[0].isalpha():
            esc = r"(?<![^\W\d_])" + esc
        if t[-1].isalpha():
            esc = esc + r"(?![^\W\d_])"
        alts.append(esc)
    cur = "|".join(alts)
    return re.compile(
        rf"(?:(?P<pre>{cur})[{_SPACES}]?(?P<num1>{_NUM}))|(?:(?P<num2>{_NUM})[{_SPACES}]?(?P<post>{cur}))",
        re.IGNORECASE,
    )


_PRICE_RE = _marker_regex()


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str | None
    raw: str
    start: int
    end: int


def parse_number(token: str) -> Decimal | None:
    """Parse a numeric token using the separator rules in the module docstring."""
    t = _ascii_digits(token).strip()
    for sp in (*_SPACES, "'", "’"):
        t = t.replace(sp, "")
    if not t or not re.fullmatch(r"[\d.,]+", t) or not t[0].isdigit() or not t[-1].isdigit():
        return None
    has_dot, has_comma = "." in t, "," in t
    if has_dot and has_comma:
        dec = "." if t.rfind(".") > t.rfind(",") else ","
        thou = "," if dec == "." else "."
        t = t.replace(thou, "").replace(dec, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = t.split(sep)
        if len(parts) > 2:
            if not all(len(p) == 3 for p in parts[1:]):
                return None
            t = "".join(parts)
        elif len(parts[1]) == 3:
            t = "".join(parts)
        elif len(parts[1]) in (1, 2):
            t = parts[0] + "." + parts[1]
        else:
            return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _resolve(marker: str, expected: list[str]) -> str | None:
    key = marker.lower()
    candidates = _MARKERS.get(key)
    if candidates is None and marker.upper() in _ISO_CODES:
        candidates = (marker.upper(),)
    if not candidates:
        return None
    for cur in expected:
        if cur in candidates:
            return cur
    return candidates[0] if len(candidates) == 1 else None


def find_prices(text: str, expected: list[str] | None = None) -> list[Money]:
    """All prices in `text` that carry a currency marker."""
    expected = expected or []
    out: list[Money] = []
    for m in _PRICE_RE.finditer(_ascii_digits(text)):
        marker = m.group("pre") or m.group("post")
        num = m.group("num1") or m.group("num2")
        if marker.lower() in {"r", "rs", "rs.", "le", "tl", "ft", "lei"} and not any(
            c in _MARKERS[marker.lower()] for c in expected
        ):
            # Short ambiguous words only count as markers when the country expects them.
            continue
        amount = parse_number(num)
        if amount is None:
            continue
        raw = text[m.start() : m.end()]
        out.append(Money(amount, _resolve(marker, expected), raw, m.start(), m.end()))
    return out


def parse_money(text: str, expected: list[str] | None = None) -> Money | None:
    """First price in `text`, preferring one whose currency is expected."""
    found = find_prices(text, expected)
    if not found:
        return None
    if expected:
        for money in found:
            if money.currency in expected:
                return money
    return found[0]


def parse_bare_amount(text: str) -> Decimal | None:
    """Parse a price string that has no currency marker (e.g. JSON field "129,00")."""
    m = re.search(_NUM, _ascii_digits(text))
    return parse_number(m.group(0)) if m else None
