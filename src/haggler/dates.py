"""Calendar dates written in page text (es / pt / sv / en), for value-vs-quote checks.

`find_dates` reads:
  * ISO dates: "2026-12-31", "2026/12/31";
  * numeric day-first dates: "31/12/2026", "31-12-2026", "31.12.2026" (month-first only when the
    day-first reading is impossible: "12/31/2026");
  * day, month name, year: "31 de diciembre de 2026", "31 de dezembro de 2026", "1 april 2026",
    "31 December 2026", "1° de enero del 2027", "1ro de enero de 2027", "1st April, 2026",
    "31-dic-2026";
  * month name, day, year: "December 31, 2026", "diciembre 31 de 2026".
Month names may be abbreviated ("dic.", "dez", "okt", "Sept."), case and accents do not matter
("MARCO", "março"), and surrounding words are free ("fr.o.m. 1 april 2026", "até 31 de dezembro de
2026", "t.o.m. 31 mars 2027", "valid until December 31, 2026").

The year is REQUIRED and has four digits: "31 de diciembre" or "31/12" never yields a date (the
year would be a guess), nor does "31/12/26".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

# Folded (lower case, no accents) month names and abbreviations -> month number.
_MONTHS: dict[str, int] = {}
for _number, _names in enumerate(
    (
        "enero january januari janeiro jan ene",
        "febrero february februari fevereiro feb fev",
        "marzo march mars marco mar",
        "abril april apr abr",
        "mayo may maj maio mai",
        "junio june juni junho jun",
        "julio july juli julho jul",
        "agosto august augusti aug ago",
        "septiembre setiembre september setembro sept sep set",
        "octubre october oktober outubro oct okt out",
        "noviembre november novembro nov",
        "diciembre december dezembro dec dic dez",
    ),
    start=1,
):
    for _name in _names.split():
        _MONTHS[_name] = _number

_MONTH = "(?P<month>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
# Not glued to other letters: "mar" in "marca" or "may" in "mayor" is not a month.
_ALPHA_BEFORE = r"(?<![^\W\d_])"
_ALPHA_AFTER = r"(?![^\W\d_])"
# Ordinal marks after the day: "1°", "1º", "1st", "1er", "1:a"; Latin American "1ro", "2do".
_DAY = (
    r"(?<!\d)(?P<day>[0-3]?\d)"
    r"(?:(?:\s*(?:°|º|ª|\.º|st|nd|rd|th|er|:a|:e)|ro|ero|do|to|mo|vo|no)" + _ALPHA_AFTER + r")?"
)
_YEAR = r"(?P<year>(?:19|20|21)\d\d)(?!\d)"
_SEP = r"(?:\s*,\s*|\s+|\s*-\s*|\.\s*)"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 2026-12-31, 2026/12/31
    re.compile(
        r"(?<![\d/.-])(?P<year>(?:19|20|21)\d\d)([-/])(?P<month>[01]?\d)\2(?P<day>[0-3]?\d)(?!\d)"
    ),
    # 31/12/2026, 31-12-2026, 31.12.2026
    re.compile(r"(?<![\d/.-])(?P<day>[0-3]?\d)([/.-])(?P<month>[0-3]?\d)\2" + _YEAR),
    # 31 de diciembre de 2026, 1 april 2026, 1st April, 2026, 31-dic-2026
    re.compile(
        _DAY
        + r"(?:\s*de\s+|\s+of\s+|\s*-\s*|\.?\s*)"
        + _ALPHA_BEFORE
        + _MONTH
        + _ALPHA_AFTER
        + r"(?:\s*,?\s*(?:de|del)\s+|"
        + _SEP
        + r")"
        + _YEAR
    ),
    # December 31, 2026, diciembre 31 de 2026
    re.compile(
        _ALPHA_BEFORE
        + _MONTH
        + _ALPHA_AFTER
        + r"\s*"
        + _DAY
        + r"(?:\s*,?\s*(?:de|del)\s+|"
        + _SEP
        + r")"
        + _YEAR
    ),
)
_NUMERIC_MONTH = (0, 1)  # the patterns whose month group is a number


@dataclass(frozen=True)
class FoundDate:
    value: date
    start: int
    end: int
    raw: str


def _fold(text: str) -> str:
    """Lower case without accents, one character for one so match offsets stay valid."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[0]
        low = base.lower()
        out.append(low if len(low) == 1 else base)
    return "".join(out)


def _make(year: str, month: int, day: str) -> date | None:
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def find_dates(text: str) -> list[FoundDate]:
    """Every dated (day, month AND year) mention in `text`, in reading order."""
    folded = _fold(text)
    hits: list[FoundDate] = []
    for k, pattern in enumerate(_PATTERNS):
        for m in pattern.finditer(folded):
            if k in _NUMERIC_MONTH:
                value = _make(m["year"], int(m["month"]), m["day"])
                if value is None and k == 1:
                    # "12/31/2026": only month-first can be read.
                    value = _make(m["year"], int(m["day"]), m["month"])
            else:
                value = _make(m["year"], _MONTHS[m["month"]], m["day"])
            if value is not None:
                hits.append(FoundDate(value, m.start(), m.end(), text[m.start() : m.end()]))
    hits.sort(key=lambda h: (h.start, -h.end))
    out: list[FoundDate] = []
    for h in hits:
        if out and h.start < out[-1].end:
            continue  # overlaps an earlier, longer reading
        out.append(h)
    return out


def contains_date(text: str, value: date) -> bool:
    return any(h.value == value for h in find_dates(text))
