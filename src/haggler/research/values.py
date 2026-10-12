"""Value-vs-quote check: does a cited excerpt really state the value a fact claims?

A quote found on an official page proves only that the page says *something*; `check_value`
proves that it says THIS value. It runs where a bundle value meets a quote: the research agent's
bundle facts (value hook of verify_subject), the watcher's price hints and, offline, the anchors
of bundles.yaml (haggler.catalog.integrity_errors, anchor text alone, no page context).

The direction that matters is a false ACCEPTANCE (a wrong price shown as confirmed): whenever a
rule cannot tell what an amount is, the value is not confirmed and a person checks it by hand.

Kinds (what H8's bundle fields map to):
  * money / intro_money / base_money (bundle_price / bundle_intro_price / bundle_base_price):
    a value like "21900 COP/month" (haggler.money.parse_money_code), or a Decimal with the
    `currency` and `period` arguments. The prices of the quote, plus its context lines
    (Page.locate: 2 lines before, 1 after) when given, are read with
    haggler.sources.heuristics.classify_prices (amounts, currencies, periods) and given a role
    (see "Roles" below). Inside the quote's span there must be:
      - the same amount, in the bundle's currency. A bare "$" is read as the bundle's currency
        only when that currency is written "$" (COP, MXN, ARS, CLP, USD ...: find_prices'
        `expected`), never for a SEK or BRL bundle, and a currency written next to it wins
        ("$ 399 MXN", "MXN $399", "(USD): $ 19.99", "$ 399 pesos mexicanos", "$ 19.99
        dólares"), as does a currency line of the text ("Precios en dólares (USD)"). With `url`
        and `provider`, the page itself must be on the provider's domains for the bundle's
        country (a Mexican "$ 399" page never confirms a Colombian price);
      - the same period, when the quote states one ("/año" never confirms a monthly price); a
        rate the period markers do not read ("a la semana", "/día", "cada 6 meses", "per
        kvartal", "semestral") is a period too, and the monthly equivalent of an annual or
        instalment plan ("facturado anualmente", "equivale a", "12 cuotas de", "vid
        årsbetalning", or "anual" on the heading or line above) never confirms a monthly value;
      - the role "regular" ("intro" for intro_money);
      - a price stated before tax ("+ IVA", "sin IVA", "exkl. moms") never confirms a bundle
        whose price includes it (tax_included not false);
      - the bundle's own tier named for the amount: its aliases or name (the base plan's name
        for base_money). The other tiers are the other bundles with the same provider and
        country, the base plan's name for money and the bundle's own names for base_money; a
        bundle with no other tier STILL needs its own name (a Meli+ Mega page also lists Total
        and Esencial). The text is cut into clauses (line breaks, sentence ends, ";", ", " and
        the conjunctions "y / e / o / ou / och / eller / and / or"); the tier named in the
        amount's own clause decides, before or after the amount ("215 kr i månaden för det
        personliga abonnemanget och 295 kr ... för familjeabonnemanget"); a clause without a
        tier name takes, outside a card (below), the nearest earlier clause that has one (a
        heading that also states a price), never a later one (the next card's heading), and
        never when the amount's own clause states another price (a table row "Precio $ 9.900
        $ 21.900" under one heading). Either way, a tier name and the amount are paired only
        when no other price of the currency lies between them (a regular or offer price with
        another amount, or a struck-through price read by position), nor, in the amount's
        sentence, a name inherited by another tier ("Meli+ Total: todo lo de Meli+ Esencial,
        que cuesta $ 9.900"): the tier a sentence named before "$62.900 ... y el plan Premium
        $86.900" is not Premium's. A clause naming two tiers confirms neither. An inherited
        name ("todos los beneficios de Meli+ Esencial", "everything in Individual") is not a
        tier name of another clause's amount, but is one of an amount in its own clause
        ("Disfruta todos los beneficios de Meli+ Esencial por $ 9.900/mes"), and a name
        compared with ("más altos que los de AI Pro", "than", "jämfört med") is never one.
        Cards: a line that names exactly one tier (own or another; no inherited or compared
        name) and states no amount, of any currency, is a card heading ("Plan Total"). An
        amount on a line below it, up to the next line that names a tier (own or another, a
        heading or not, the amount's own line included), is the heading's tier when no other
        price lies between them, as above, with one exception: the struck price of an
        introductory amount's own offer (struck through by position by an intro of the same
        amount right after it, with no word before it on its line), between them or in the
        amount's clause, does not separate the heading from that amount ("Plan Total" /
        "$ 21.900 9% OFF" / "$ 19.900/mes por 2 meses" is Total's intro). Any other price
        still does: the card's own regular price before an add-on's, a payment variant's or a
        footer's, a tier the catalog does not list ("Plan Mega" / "$ 399/mes" / "Plan Total" /
        "$ 149/mes" is not Mega's $ 149 when Total is not a known tier), a line with words
        before its price right above an intro ("Agrega Disney+ por $ 8.000/mes", a threshold
        "desde $ 30.000"), a struck price before a regular amount (under "Meli+ Total" /
        "$ 24.900", a "$ 21.900/mes" is not its regular price: a discount whose duration the
        card does not state, or another card's price), and any other price in the amount's own
        clause (the table row). A line naming two tiers, a tier and
        an amount, or no tier ("Planes", "Elige tu plan de Meli+") heads no card: below it, and
        on a line that names a tier, the clause rules above decide. A card is read in the text
        checked only (the quote and its context lines): a heading outside it heads nothing.
    An amount needs its currency marker: a PDF row with bare amounts under a currency header
    ("Go3 Filmai + Netflix Premium 19,99") states no price.
    Then plausibility, with an FX table: a monthly equivalent outside 0.5-300 EUR is
    "implausible" (an intro price may be 0); without one the check is skipped and the reason
    says so. A change of more than 50 % of the monthly equivalent against `current` (the YAML
    value: a Decimal in the bundle's period, or the same "21900 COP/month" form) sets
    big_change; the status stays ok. Amounts are 0 <= amount < 10^12 with at most 6 decimals;
    anything else is not an amount, on either side.
  * int / pct / months: the number is a number token of the quote, not part of a larger number
    ("2" is not in "$ 21.900", "12" not in "31/12/2026", "7" not in "24/7", "12" not in
    "12:00") nor of a date, not glued to letters ("4K"), and without a leading zero ("06"); 2-12
    also count as words (es/pt/sv/en: "dos", "seis", "sex", "six"; Spanish "once" is left out,
    it is also English, and "dez" before a year is December). int is a count that is neither a
    percentage nor an amount of money; pct needs "%", "por ciento", "por cento", "procent" or
    "percent" right after the number; months (bundle_intro_months, bundle_contract_months)
    needs a month unit right after it ("2 meses", "6 månader", "los 2 primeros meses") or
    "meses: 2" before it.
  * date / date_from / date_until: haggler.dates.find_dates finds that date in the quote (the
    year is required). date_until (offer / intro end dates) rejects a date the text gives as a
    start ("desde", "a partir del", "fr.o.m.", "from"), date_from one it gives as an end
    ("hasta", "até", "t.o.m.", "until"); date takes either.
  * component: "service_id", "service_id.plan_id" or "service_id:N" (a discount or a count of
    free months). The catalog name of the service or one of its aliases appears in the quote
    (accents ignored, whole words; case ignored too, except for aliases of three letters or
    fewer and common words, "Max", which must be written as such and not before a number: "max
    5 personer", "máx. 4 pantallas" are not HBO Max). Then:
      - no mention may be negated: "no incluido", "não incluído", "ingår inte", "not
        included", "excluido", or "sin / sem / utan / without" right before the name, in the
        mention's clause, in the clause that heads its list ("No incluye: Disney+, HBO Max") or
        after the list's last item ("Netflix y Disney+ no están incluidos");
      - "N" must be a number token of the service's own clause, or of the nearest earlier
        clause with a number when only bare list items (names) lie between them in the same
        sentence and that number is not another service's ("30% OFF en HBO Max, Universal+,
        Paramount+ y ViX Premium": 30 for all four; never from "Disney+ con 30% OFF, además
        HBO Max" nor across a sentence end), or of the next clause of the sentence when that
        clause names no other service; "20% OFF en Disney+ y 30% OFF en HBO Max" gives Disney+
        20 only;
      - a value without ":N" claims the service is INCLUDED: the number the clause rule above
        gives it must not be a discount ("%", "OFF", "descuento", "rabatt") nor a count of free
        months, and the clause must carry no discount word;
      - with a bundle, the tier rule of the money kinds (without the in-between-price
        condition) applied to the service's mention, with the context lines when given: a
        clause naming ANOTHER tier, or several, fails ("Meli+ Total: incluye Disney+" never
        confirms Meli+ Esencial); naming none passes (component lines rarely name the tier);
      - a plan is checked when the text names one right after the service's name ("Netflix
        Standard", "HBO Max Basic med reklam") or right before it ("el plan Básico con
        anuncios de HBO Max"): an ads marker after a plan word ("med reklam", "con anuncios",
        "with ads") names that plan's ads variant; an ads marker alone names an ads plan; a
        plan word of the catalog that no plan of the service has ("Paramount+ Básico") is an
        unknown plan. Naming ANOTHER plan, or an unknown one, fails; naming none passes (plan
        aliases and labels are incomplete across languages).
  * discount_pct (bundle_discount): a component value "service_id:N" whose N, found by the
    component rule above, must also be a percentage ("%", "por ciento", "procent", "percent"
    right after it): "2 meses gratis de HBO Max", "HBO Max para 2 dispositivos" and "Ahorra
    US$ 5 en HBO Max" give HBO Max a number but no percentage discount.
  * url: a URL has nothing to find in a quote: ok only when it is https on the provider's
    domains for the bundle's country (haggler.catalog.on_domains).

A quote and its context are checked up to MAX_CHECK_CHARS characters; a longer text is not
checked (value_not_in_quote).

Roles. classify_prices' own roles are recomputed here with the same predicates
(haggler.sources.heuristics.is_savings / is_comparison / is_intro), with refinements that only
apply to value checks (the daily price pipeline keeps its own rules):
  * savings, discount and tax amounts are no price: "ahorro de / economia de / descuento de /
    dto. / Rabatt / Besparing: $ X", "$ X de descuento / OFF / rabatt / billigare", "Spara
    58/77/215 kr", an amount glued to "%" ("$ 9910% OFF"), "IVA: / Moms: / varav moms / (IVA
    incluido $ X)" ("con IVA:" is the price);
  * value and sum words make a comparison price: "valor total", "valorizados en", "que valen",
    "värde", "por separado", "si los compras separadamente", "en lugar de pagar", "istället för
    att betala", "instead of paying", "vs", besides classify_prices' "not / inte / instead of /
    separately / value of";
  * a price per person or per extra member ("($ 10.483 por persona)", "49 kr/mån per
    familjemedlem", "extra användare för 49 kr") is not the bundle's;
  * a line break ends what may describe a price: an introductory duration on the NEXT line
    ("$ 19.900/mes" / "por 2 meses", or "295 kr/mån" / "Prova gratis i 1 månad") cannot be
    told apart from the next item's, so the price is an "offer" that confirms neither a
    regular nor an intro value; labels on the previous line ("Antes" / "$ 24.900") still
    count;
  * an introductory price is structural, never an offer word alone: a duration tied to the
    price (classify_prices' patterns, plus "los primeros 2 meses", "(2 meses)", "de första 6
    månaderna", "i 6 mån", "nos 2 primeiros meses", "hasta 3 meses", "el / tu / durante el
    primer mes", "första månaden" after it and "2 meses a" before it). An offer without one
    makes the price an "offer": an offer word earlier in the price's own line and clause
    ("Precio especial: $ 19.900", "Oferta", "Prova gratis", "con descuento") or later in its
    sentence ("precio promocional", "con 9% de descuento"), an end date in its sentence ("hasta
    el 31 de diciembre"), or a lower bound right before it ("desde", "a partir de", "från",
    "from": also free-shipping thresholds). "envíos gratis", "frete grátis", "free shipping"
    and "ad-free" are not offer words;
  * a price repeated with no duration where the text states the same amount as an
    introductory one ("R$ 15,90 /mês por 2 meses" / "Assinar por R$ 15,90") is an "offer";
  * two prices with nothing but blanks, a line break or a "-12%" / "12% OFF" badge between
    them, the second lower and the first unlabeled ("$ 24.900" / "$ 21.900/mes"): the first is
    a struck-through price whose markup was lost, a "compare" price;
  * a lower price on the line of an ordinary price ("Ordinarie pris: 269 kr/mån" ... "199
    kr/mån", or on the line before or after) is a discounted price: an "offer" unless its
    duration makes it the intro;
  * the previous-price rule below.

Decision ("precio anterior" next to an introductory price): pages state an introductory offer
together with the price that follows it, labeled as the previous or ordinary price ("Precio
actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. Precio anterior:
$ 21.900 pesos por mes."; Telia: "Ordinarie pris: 269 kr/mån.Pris: 199 kr/mån.Rabatten gäller i
6 månader"). A price labeled previous ("precio anterior", "antes", "was", "tidigare", the
"De R$ 29,90 por R$ 19,90" idiom ...) or ordinary ("ordinarie pris", "precio regular / normal",
"regular price", "full price" ...) is the REGULAR price, what the subscriber pays once the offer
ends, only when the price right before or after it, on the same line, is a lower introductory
price (its duration stated as above) of the same tier, and no other regular price of that line
("luego $ X", "ahora $ X") competes with it. Nor anywhere else in the text: a price after a
then-word ("Luego $ 21.900/mes" on the next line, "Depois R$ 24,90") or another unlabeled
regular price that is not another tier's ("Después de 2 meses pagas $ 21.900 al mes.") is the
one paid after the offer, and the previous price is not confirmed. Otherwise a previous price
is a "compare" (struck-through) price, and so is an ordinary price next to any lower price of
the text; an ordinary price with no lower price around ("Ordinarie pris: 269 kr/mån.") is
simply the regular price. Comparison labels that name another product ("not", "instead of",
"separately", "value of") stay "compare" either way.
"""

from __future__ import annotations

import bisect
import functools
import itertools
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from typing import TYPE_CHECKING, Literal

from haggler.dates import MONTH_NAMES, date_direction, find_dates
from haggler.money import ISO_CODES, format_money_code, parse_money_code, parse_number
from haggler.sources.heuristics import (
    PriceInfo,
    classify_prices,
    follows_then,
    is_comparison,
    is_intro,
    is_savings,
)

if TYPE_CHECKING:
    from haggler.models.catalog import Catalog, ServiceDef
    from haggler.models.curated import Bundle, Provider
    from haggler.models.fx import FxTable

ValueKind = Literal[
    "money",
    "intro_money",
    "base_money",
    "int",
    "pct",
    "months",
    "date",
    "date_from",
    "date_until",
    "component",
    "discount_pct",
    "url",
]
ValueStatus = Literal["ok", "value_not_in_quote", "implausible"]
MONEY_KINDS: tuple[ValueKind, ...] = ("money", "intro_money", "base_money")
NUMBER_KINDS: tuple[ValueKind, ...] = ("int", "pct", "months")
DATE_KINDS: tuple[ValueKind, ...] = ("date", "date_from", "date_until")

# Monthly equivalent, in EUR, that a bundle price may have.
PLAUSIBLE_MONTHLY_EUR = (Decimal("0.5"), Decimal("300"))
# A change against the current value above this fraction is flagged for review.
BIG_CHANGE = Decimal("0.5")
# The longest quote plus context a check reads (characters); a longer one is not checked.
MAX_CHECK_CHARS = 8000
_MONTHS_PER = {"month": 1, "quarter": 3, "year": 12}
# Bounds of an amount or a number (both sides of a check).
_MAX_AMOUNT = Decimal(10) ** 12
_MIN_EXPONENT = -6


@dataclass(frozen=True)
class ValueCheck:
    """Outcome of check_value. `reason` is English, for logs and review output."""

    status: ValueStatus
    reason: str = ""
    big_change: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _ok(reason: str = "", *, big_change: bool = False) -> ValueCheck:
    return ValueCheck("ok", reason, big_change)


def _missing(reason: str) -> ValueCheck:
    return ValueCheck("value_not_in_quote", reason)


def _too_long(n: int) -> ValueCheck:
    return _missing(f"the text is too long to check ({n} characters, at most {MAX_CHECK_CHARS})")


# ---- text folding with offsets --------------------------------------------------------------

_DASHES = re.compile(r"[‐-―−]")


@functools.lru_cache(maxsize=8192)
def _fold_char(ch: str) -> str:
    """NFKC, case-folded, without accents; dashes and the typographic apostrophe made ASCII."""
    folded = unicodedata.normalize("NFKC", ch).casefold()
    folded = "".join(
        c for c in unicodedata.normalize("NFD", folded) if not unicodedata.combining(c)
    )
    return _DASHES.sub("-", folded).replace("’", "'")


def _fold_map(text: str) -> tuple[str, list[int]]:
    """`text` folded (see _fold_char) with whitespace runs collapsed to one space, and for every
    folded character the index of the character of `text` it comes from."""
    out: list[str] = []
    index: list[int] = []
    space = True  # drops leading whitespace
    for i, ch in enumerate(text):
        if ch.isspace() or ch == "\u200b":
            if not space:
                out.append(" ")
                index.append(i)
            space = True
            continue
        for c in _fold_char(ch):
            out.append(c)
            index.append(i)
        space = False
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def _fold(text: str) -> str:
    return _fold_map(text)[0]


@functools.lru_cache(maxsize=4096)
def _name_pattern(name: str) -> re.Pattern[str] | None:
    folded = _fold(name)
    if not folded:
        return None
    body = re.escape(folded).replace(r"\ ", r"\s+")
    # Whole words: no letter or digit glued to an alphanumeric end ("Max" is not in "Maxad").
    before = r"(?<![^\W_])" if folded[0].isalnum() else ""
    after = r"(?![^\W_])" if folded[-1].isalnum() else ""
    return re.compile(before + body + after)


def _mentions(folded: str, names: Iterable[str]) -> list[tuple[int, int, str]]:
    """(start, end, folded name) in `folded` for every mention of one of `names`; the longest
    name wins an overlap."""
    hits: list[tuple[int, int, str]] = []
    for name in dict.fromkeys(names):
        if (rx := _name_pattern(name)) is not None:
            key = _fold(name)
            hits.extend((m.start(), m.end(), key) for m in rx.finditer(folded))
    hits.sort(key=lambda h: (h[0] - h[1], h[0]))
    starts: list[int] = []
    kept: list[tuple[int, int, str]] = []  # sorted by start, never overlapping
    for s, e, name in hits:
        i = bisect.bisect_left(starts, s)
        if (i > 0 and kept[i - 1][1] > s) or (i < len(kept) and kept[i][0] < e):
            continue
        starts.insert(i, s)
        kept.insert(i, (s, e, name))
    return kept


# ---- clauses --------------------------------------------------------------------------------

# Where a clause ends, for pairing a tier name or a discount with its amount: a line break, a
# sentence end ("." before a space, the end or a capital: "kr/mån.Pris"; between a letter and a
# digit: "ViX Premium.30% OFF"), "!", "?", ";", "•", "|", a comma before a space, and the
# conjunctions (es / pt / sv / en).
_CLAUSE_BREAK = re.compile(
    r"\n|[!?;•|]|\.(?=\s|$|[A-ZÀ-ÖØ-Þ])|(?<=[^\W\d_])\.(?=\d)|,(?=\s)"
    r"|(?i:\s(?:y|e|o|ou|och|eller|samt|and|or)(?=\s))"
)
_SENTENCE_BREAKS = frozenset("\n!?;•|.")


@dataclass(frozen=True)
class _Clauses:
    """The clauses of a text: their start offsets, and whether each one starts a sentence."""

    starts: list[int]
    sentence: list[bool]
    length: int

    @classmethod
    def of(cls, text: str) -> _Clauses:
        starts, sentence = [0], [True]
        for m in _CLAUSE_BREAK.finditer(text):
            starts.append(m.end())
            sentence.append(m.group() in _SENTENCE_BREAKS)
        return cls(starts, sentence, len(text))

    def of_pos(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos) - 1

    def span(self, k: int) -> tuple[int, int]:
        end = self.starts[k + 1] if k + 1 < len(self.starts) else self.length
        return self.starts[k], end

    def __len__(self) -> int:
        return len(self.starts)


# ---- numbers --------------------------------------------------------------------------------

# A number not glued to a larger one: no digit, decimal separator or "digit/" "digit-" "digit:"
# before it, no digit, "/digit" "-digit" ".digit" ":digit" or letter after it ("31/12/2026",
# "24/7", "01-8000-30", "12:00", "4K"). A period right after a letter ends a sentence glued to
# the number ("ViX Premium.30% OFF": Mercado Libre prints each line twice, glued), so it is no
# decimal separator.
_NUMBER_TOKEN = re.compile(
    r"(?:(?<![\d.,])|(?<=[^\W\d_]\.))(?<!\d[/\-:])\d+(?:[.,]\d+)*(?!\d|[.,/\-:]\d)(?![^\W\d_])"
)
# Folded number words (es / pt / sv / en). 1 is left out: "un", "en", "ett" are also articles
# and prepositions; Spanish "once" (11) is too, it is also English ("billed once a year").
_NUMBER_WORDS: dict[int, tuple[str, ...]] = {
    2: ("dos", "dois", "duas", "tva", "two"),
    3: ("tres", "tre", "three"),
    4: ("cuatro", "quatro", "fyra", "four"),
    5: ("cinco", "fem", "five"),
    6: ("seis", "sex", "six"),
    7: ("siete", "sete", "sju", "seven"),
    8: ("ocho", "oito", "atta", "eight"),
    9: ("nueve", "nove", "nio", "nine"),
    10: ("diez", "dez", "tio", "ten"),
    11: ("onze", "elva", "eleven"),
    12: ("doce", "doze", "tolv", "twelve"),
}
_WORD_VALUE = {w: n for n, words in _NUMBER_WORDS.items() for w in words}
# "dez" before a year is December ("até dez/2026", "Dez. 2026").
_DEZ_MONTH = re.compile(r"\s*[/.\-]?\s*(?:de\s+)?(?:19|20)\d\d(?!\d)", re.IGNORECASE)
_PERCENT_AFTER = re.compile(
    r"\s*(?:%|(?:por\s+ciento|por\s+cento|procent|percent|pct)(?![^\W\d_]))", re.IGNORECASE
)
_MONTH_UNIT_AFTER = re.compile(
    r"(?:\s*-\s*|\s+)(?:(?:primeros?|primeras?|primeiros?|primeiras?|first|f[öo]rsta)\s+)?"
    r"(?:mes(?:es)?|m[eê]s|m[åa]n(?:ad(?:er(?:na)?|en)?)?\.?|months?|mo\.?)"
    r"(?-i:(?![a-zß-öø-ÿ0-9_]))",
    re.IGNORECASE,
)
_MONTH_UNIT_BEFORE = re.compile(
    r"(?:meses|mes|months?|m[åa]nader|m[åa]n|mês)\s*[:=]\s*$", re.IGNORECASE
)
_CURRENCY_AFTER = re.compile(
    r"\s*(?:kr\.?|sek|nok|dkk|eur|euros?|€|usd|us\$|\$|r\$|cop|mxn|brl|ars|clp|pesos?|reais"
    r"|kronor|:-)(?![^\W\d_])",
    re.IGNORECASE,
)
_CURRENCY_BEFORE = re.compile(
    r"(?:\$|€|£|r\$|us\$|(?<![^\W\d_])(?:cop|mxn|brl|ars|clp|sek|eur|usd|kr))\s*$", re.IGNORECASE
)

NumberUnit = Literal["any", "int", "pct", "months"]


def _bounded(d: Decimal | None) -> Decimal | None:
    """`d` when it is a finite amount or number in bounds (0 <= d < 10^12, at most 6 decimals),
    else None."""
    if d is None or not d.is_finite() or not (0 <= d < _MAX_AMOUNT):
        return None
    if d == 0:
        return Decimal(0)
    exponent = d.as_tuple().exponent
    if isinstance(exponent, int) and exponent < _MIN_EXPONENT:
        if d.adjusted() < _MIN_EXPONENT:
            return None
        d = d.normalize()
        exponent = d.as_tuple().exponent
        if isinstance(exponent, int) and exponent < _MIN_EXPONENT:
            return None
    return d


def _decimal(value: object) -> Decimal | None:
    """A Decimal in bounds from a number or numeric text ("30", "30 %", "21.900"); None
    otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal | int):
        try:
            d: Decimal | None = Decimal(value)
        except (InvalidOperation, ValueError):
            return None
    else:
        text = str(value).strip().rstrip("%").strip()
        if len(text) > 40:
            return None
        try:
            d = Decimal(text)
        except InvalidOperation:
            d = parse_number(text)
    return _bounded(d)


def _blank_dates(text: str) -> str:
    """`text` with every dated mention (find_dates) replaced by spaces; offsets are kept."""
    for d in find_dates(text):
        text = text[: d.start] + " " * (d.end - d.start) + text[d.end :]
    return text


def _number_hits(text: str) -> list[tuple[Decimal, int, int]]:
    """(value, start, end) of every number of `text` outside its dates: tokens without a leading
    zero and number words (see the module docstring)."""
    blank = _blank_dates(text)
    hits: list[tuple[Decimal, int, int]] = []
    for m in _NUMBER_TOKEN.finditer(blank):
        tok = m.group()
        if len(tok) > 1 and tok[0] == "0" and tok[1].isdigit():
            continue
        if (n := _bounded(parse_number(tok))) is not None:
            hits.append((n, m.start(), m.end()))
    folded, index = _fold_map(blank)
    for s, e, word in _mentions(folded, _WORD_VALUE):
        start, end = index[s], index[e - 1] + 1
        if word == "dez" and _DEZ_MONTH.match(blank, end):
            continue
        hits.append((Decimal(_WORD_VALUE[word]), start, end))
    return sorted(hits, key=lambda h: h[1])


def _unit_ok(text: str, start: int, end: int, unit: NumberUnit) -> bool:
    if unit == "any":
        return True
    if unit == "pct":
        return bool(_PERCENT_AFTER.match(text, end))
    if unit == "months":
        return bool(
            _MONTH_UNIT_AFTER.match(text, end) or _MONTH_UNIT_BEFORE.search(text[:start][-20:])
        )
    # int: a count, neither a percentage nor an amount of money.
    return not (
        _PERCENT_AFTER.match(text, end)
        or _CURRENCY_AFTER.match(text, end)
        or _CURRENCY_BEFORE.search(text[:start][-6:])
    )


def _numbers_in(text: str, unit: NumberUnit = "any") -> set[Decimal]:
    """The numbers of `text` (see _number_hits) that carry `unit`."""
    return {n for n, s, e in _number_hits(text) if _unit_ok(text, s, e, unit)}


# ---- money: roles ---------------------------------------------------------------------------

# Labels that mark the price after them as the previous one or the ordinary one (module
# docstring, "Decision"). Markup may glue a capitalized label to the text before it
# ("269 kr/månOrdinarie pris: 269 kr/mån").
_LABEL_START = r"(?:(?<!\w)|(?-i:(?<=[a-zß-öø-ÿ])(?=[A-ZÀ-ÖØ-Þ])))"
_PREVIOUS_BEFORE = re.compile(
    _LABEL_START + r"(?:precio\s+(?:anterior|original)(?:\s+de)?|pre[çc]o\s+(?:anterior|original)"
    r"(?:\s+de)?|antes|original\s+price|was|previously|originally|tidigare(?:\s+pris)?|förut"
    r"|vorher|ehemals)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
_ORDINARY_BEFORE = re.compile(
    _LABEL_START + r"(?:precio\s+(?:regular|normal|habitual|de\s+lista)(?:\s+de)?"
    r"|pre[çc]o\s+(?:normal|regular|cheio|de\s+tabela)(?:\s+de)?|ordinarie(?:\s+pris)?"
    r"|ord\.?\s*pris|normalpris|normalt\s+pris|ordinary\s+price|regular(?:ly)?\s+pric(?:e|ed)"
    r"(?:\s+(?:of|at))?|usual(?:ly)?\s+price|full\s+price)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# "De R$ 29,90 por R$ 19,90", "De $ 24.900 a $ 19.900" (pt / es): the first price is the old one.
_DE_BEFORE = re.compile(r"(?<![^\W\d_])de\s*:?\s*$", re.IGNORECASE)
_DE_BETWEEN = re.compile(r"^\s*(?:/\s*[^\s\d/]{1,8}\.?)?\s*(?:por|a|para|pra)\s*$", re.IGNORECASE)

# Introductory durations classify_prices does not read (module docstring, "Roles").
_V_N = (
    r"(?:\d{1,2}|un|una|uno|um|uma|en|ett|one|dos|dois|duas|två|two|tres|três|tre|three"
    r"|cuatro|quatro|fyra|four|cinco|fem|five|seis|sex|six|doce|doze|tolv|twelve)"
)
# A unit may be glued to the next sentence ("6 månaderIngen"), never to a lower-case letter.
_V_UNIT = (
    r"(?:months?|mes(?:es)?|mês|mån(?:ad(?:er(?:na)?|en)?)?\.?|weeks?|semanas?|veckor(?:na)?"
    r"|veckan?|days?|d[ií]as?|dagar(?:na)?)(?-i:(?![a-zß-öø-ÿ0-9_]))"
)
_FIRST = r"(?:primeros?|primeras?|primeiros?|primeiras?|first|första)"
_COUNT = rf"(?:{_FIRST}\s+)?{_V_N}(?:\s+{_FIRST})?\s+{_V_UNIT}"
_FIRST_COUNT = rf"(?:{_FIRST}\s+{_V_N}|{_V_N}\s+{_FIRST})\s+{_V_UNIT}"
_PREP = (
    r"(?:durante|por|para|for|in|i|under|nos|nas|x|during|hasta|até|ate|up\s+to"
    r"|upp\s+till)"
)
_ARTICLE = r"(?:los|las|the|de|os|as)"
# "el primer mes", "tu primer mes", "durante el primer mes", "no primeiro mês", "första månaden".
_FIRST_MONTH = (
    r"(?:(?:durante|during|por|for|in|i|no|na|en)\s+)?(?:(?:el|tu|su|the|your|den|o|seu|la)\s+)?"
    r"(?:primer|primero|first|första|primeiro|1er|1ro)\s+(?:mes|month|månaden|månad|mån|mês)"
    r"(?-i:(?![a-zß-öø-ÿ0-9_]))"
)
# Right after the price: "/mes los primeros 2 meses", " (2 meses)", "/mån i 6 mån", " el primer
# mes", "/mes hasta 3 meses".
_INTRO_TAIL = re.compile(
    rf"^\s*(?:/\s*[^\s\d/]{{1,8}}\.?)?\s*(?:\(\s*(?:{_ARTICLE}\s+)?{_COUNT}\s*\)"
    rf"|,?\s*(?:{_PREP}\s+)?{_ARTICLE}\s+{_FIRST_COUNT}|,?\s*{_PREP}\s+(?:{_ARTICLE}\s+)?{_COUNT}"
    rf"|,?\s*{_FIRST_MONTH})",
    re.IGNORECASE,
)
# Right before the price: "2 meses a ", "los primeros 2 meses: ", "6 mån för ".
_INTRO_LEAD = re.compile(
    rf"(?<![^\W\d_])(?:{_COUNT}\s+(?:a|por|para|for|för|at|à)(?:\s+(?:solo|sólo|só|apenas|only|bara))?"
    rf"|{_FIRST_COUNT})\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# Offer words in the price's own line and clause (an offer without a stated duration).
_OFFER_WORD = re.compile(
    r"(?<![\w-])(?:free|gratis|grátis|gratuit\w*|trial|prueba|teste|provperiod\w*|prova"
    r"|first|primer|primeir[oa]|första|intro\w*|special|especial|oferta\w*|erbjudande\w*"
    r"|offer\w*|promo\w*|kampanj\w*|descuentos?|descontos?|rabatt\w*|rebajad\w*|rebaixad\w*"
    r"|discount\w*)(?![\w-])",
    re.IGNORECASE,
)
# ... and later in its sentence ("$ 19.900/mes precio promocional", "con 9% de descuento"; free
# items are listed after prices, so "gratis" / "free" do not count there).
_OFFER_WORD_AFTER = re.compile(
    r"(?<![\w-])(?:trial|prueba|teste|provperiod\w*|intro\w*|special|especial|oferta\w*"
    r"|erbjudande\w*|offer\w*|promo\w*|kampanj\w*|descuentos?|descontos?|rabatt\w*"
    r"|rebajad\w*|rebaixad\w*|discount\w*|reducid\w*|nedsatt)(?![\w-])",
    re.IGNORECASE,
)
# ... except where the word is about something else ("envíos gratis", "free shipping").
_NOT_OFFER_BEFORE = re.compile(
    r"(?<![^\W\d_])(?:env[ií]os?|frete|entrega|despacho|shipping|delivery|frakt|leverans|ads?)"
    r"[\s-]*$",
    re.IGNORECASE,
)
_NOT_OFFER_AFTER = re.compile(r"^[\s-]*(?:shipping|delivery|frakt|leverans)", re.IGNORECASE)
# A lower bound right before the price ("desde $ 19.900", "a partir de R$ 19", "från 99 kr").
_FROM_BEFORE = re.compile(
    r"(?<![^\W\d_])(?:desde|a\s+partir\s+de|fr[åa]n|from|starting\s+(?:at|from)|starts\s+at)"
    r"\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# An end date in the price's sentence (folded text): "hasta el 31 de diciembre", "até 31/12".
_UNTIL_DATE = re.compile(
    r"(?<![^\W\d_])(?:hasta|ate|t\.?\s*o\.?\s*m\.?|till\s+och\s+med|until|till|through)\s+"
    r"(?:(?:el|o|den|the|dia|day)\s+)*(?:\d{1,2}(?:\s*(?:o|º|°|ro|er|st|nd|rd|th|:a|:e))?\.?\s*"
    r"(?:[/.\-]\s*\d{1,2}|(?:de\s+|of\s+)?(?:" + "|".join(MONTH_NAMES) + r")(?![^\W\d_]))"
    r"|(?:" + "|".join(MONTH_NAMES) + r")(?![^\W\d_])|\d{4}-\d\d-\d\d)"
)
_SENTENCE = re.compile(r"[.!?;•|]\s")
# A sentence end after a price: "." before a capital ("kr/mån. Prova", "kr/mån.Rabatten", but not
# "/mo. for 3 months"), "!", "?", ";", "•", "|".
_SENTENCE_END = re.compile(r"\.(?=\s*[A-ZÀ-ÖØ-Þ])|[!?;•|]")
# The start of a sentence that speaks of the price just stated ("Rabatten gäller i 6 månader",
# "El descuento aplica por 2 meses", "Durante 3 meses."): a duration in it is that price's.
_BACK_REFERENCE = re.compile(
    rf"^\s*(?:(?:rabatt\w*|erbjudandet|kampanj\w*|priset|(?:el|este|o|the|this)\s+(?:descuento"
    rf"|desconto|precio|pre[çc]o|discount|offer|price)|(?:la|esta|a|esa)\s+(?:oferta|promoci[óo]n"
    rf"|promo[çc][ãa]o)|descuento|desconto|discount|v[áa]lid[oa]?|g[äa]ller|aplica|applies)"
    rf"(?![^\W\d_])|{_PREP}\s+(?:{_ARTICLE}\s+)?{_COUNT})",
    re.IGNORECASE,
)

# Savings and discount amounts the heuristics do not read (module docstring, "Roles").
_V_SAVINGS_BEFORE = re.compile(
    r"(?<![^\W\d_])(?:ahorr[oa]s?|economias?|economi[sz]e|descuentos?|descontos?|dto\.?"
    r"|rabatt\w*|besparing\w*|savings?|discount)"
    r"(?:\s+(?:mensual(?:es)?|mensal|mensais|de|del|do|da|of|på|av|total|hasta|até"
    r"|up\s+to|upp\s+till))*\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# ... unless the noun describes the price itself ("Precio actual con 9 por ciento de
# descuento: 19900 pesos", "con descuento: $ 19.900").
_PRICE_LEAD = re.compile(
    r"(?<![^\W\d_])(?:con|com|med|with|precio|pre[çc]o|pris|price|tarifa)(?![^\W\d_])",
    re.IGNORECASE,
)
_SAVE_LIST_BEFORE = re.compile(
    r"(?<![^\W\d_])(?:spara|sparar|ahorr\w*|save|saves|economiz\w*)\s+(?:[\d.,]+\s*/\s*)+$",
    re.IGNORECASE,
)
_V_SAVINGS_AFTER = re.compile(
    r"^(?:%|\s*(?:/\s*[^\s\d/]{1,8}\.?\s*)?(?:(?:i|in|en|of|de|da|del|do)\s+)?"
    r"(?:\w*rabatt\w*|descuentos?|descontos?|dto(?![^\W\d_])|off(?![^\W\d_])|ahorro"
    r"|economia|besparing\w*|billigare|l[äa]gre|lower|m[aá]s\s+barato|mais\s+barato|cheaper"
    r"|savings?))",
    re.IGNORECASE,
)
_V_COMPARE_BEFORE = re.compile(
    r"(?<![^\W\d_])(?:valor(?:es)?\s+(?:total|real|comercial)(?:\s+(?:de|do|da))?"
    r"|valorizad\w*\s+en|avaliad\w*\s+em|que\s+valen|que\s+valem|que\s+vale"
    r"|v[äa]rde(?:\s+av)?|v[äa]rt|värderat\s+till|worth|valued\s+at"
    r"|(?:por\s+separado|separadamente|separat\w*|separately)(?:\s+[^\W\d_]+){0,2}"
    r"|en\s+lugar\s+de(?:\s+pagar)?|en\s+vez\s+de(?:\s+pagar)?|ao\s+inv[ée]s\s+de(?:\s+pagar)?"
    r"|em\s+vez\s+de(?:\s+pagar)?|ist[äa]llet\s+f[öo]r(?:\s+att\s+betala)?"
    r"|instead\s+of(?:\s+paying)?|vs\.?|versus|j[äa]mf[öo]rt\s+med|compared\s+(?:to|with)"
    r"|comparado\s+con)\s*[:\-–(]?\s*$",
    re.IGNORECASE,
)
_V_COMPARE_AFTER = re.compile(
    r"^\s*(?:/\s*[^\s\d/]{1,8}\.?\s*)?\(?\s*(?:si\s+(?:los|las|lo|la)\s+\w+\s+(?:por\s+)?"
    r"separad\w*|separad\w*|por\s+separado|por\s+aparte|de\s+valor|en\s+valor|i\s+v[äa]rde"
    r"|in\s+value)",
    re.IGNORECASE,
)
_MEMBER = (
    r"(?:personas?|usuari[oa]s?|pessoas?|persons?|personer|people|medlem\w*|familjemedlem\w*"
    r"|användare|members?|users?|perfil(?:es)?|profiles?|cuentas?|accounts?|integrantes?"
    r"|miembros?|membros?|seats?)"
)
_PER_PERSON_AFTER = re.compile(
    rf"^\s*(?:/\s*[^\s\d/]{{1,8}}\.?\s*)?(?:(?:al|por|per|a)\s+(?:mes|month|månad)\s+)?"
    rf"(?:por|per|each|cada|/|a|pr\.?)\s*(?:extra\s+|adicional\s+)?{_MEMBER}(?![^\W\d_])",
    re.IGNORECASE,
)
_PER_PERSON_BEFORE = re.compile(
    rf"(?<![^\W\d_])(?:(?:extra|additional|adicional|ytterligare)\s+{_MEMBER}"
    rf"|{_MEMBER}\s+(?:adicional\w*|extra|additional)|adicional\w*|additional)"
    rf"(?:\s+[^\W\d_]+){{0,2}}\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# Tax: a price stated before tax, and a tax amount.
_EX_TAX_AFTER = re.compile(
    r"^\s*(?:/\s*[^\s\d/]{1,8}\.?\s*)?(?:(?:al|por|per|a|i)\s+(?:mes|m[åa]naden|month)\s+)?\(?\s*"
    r"(?:\+\s*(?:iva|moms|vat|impuestos?|impostos?|tax(?:es)?)|m[aá]s\s+(?:iva|impuestos?)"
    r"|mais\s+impostos?|plus\s+(?:vat|tax|moms)|exkl\.?\s*moms|exklusive\s+moms|ex\.?\s*moms"
    r"|excl\.?\s*(?:vat|moms|tax)|exclusive\s+of\s+(?:vat|tax)|excluding\s+(?:vat|tax)"
    r"|ex\.?\s*vat|sin\s+(?:iva|impuestos)|sem\s+impostos|antes\s+de\s+(?:iva|impuestos)"
    r"|before\s+tax(?:es)?)(?![^\W\d_])",
    re.IGNORECASE,
)
_EX_TAX_BEFORE = re.compile(
    r"(?<![^\W\d_])(?:sin\s+iva|exkl\.?\s*moms|exklusive\s+moms|ex\.?\s*moms"
    r"|excl\.?\s*(?:vat|moms|tax)|excluding\s+(?:vat|tax)|sem\s+impostos"
    r"|antes\s+de\s+(?:iva|impuestos)|before\s+tax|netto(?:pris)?)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
_TAX_LABEL = re.compile(
    r"(?<![^\W\d_])(?:iva|moms|vat|impuestos?|impostos?|tax(?:es)?|varav\s+moms)"
    r"\s*[:\-–]?\s*(?:de\s+)?$",
    re.IGNORECASE,
)
_TAX_INCLUDED_LEAD = re.compile(
    r"(?<![^\W\d_])(?:con|com|inkl\.?|incl\.?|including|inklusive|med|incluido|incluye)\s*$",
    re.IGNORECASE,
)
_TAX_PAREN = re.compile(
    r"\(\s*(?:iva|vat|moms)\s+(?:incluid[oa]|included|ingår)\s*[:\-–]?\s*$", re.IGNORECASE
)
# Rates the period markers do not read (module docstring, money).
_RATE_UNIT = (
    r"(?:d[ií]as?|dag(?:en)?|days?|semanas?|veckan?|vecka|weeks?|wk|semestres?"
    r"|halv[åa]r(?:et)?|bimestres?|trimestres?|kvartal(?:et)?|quarters?)"
)
_RATE_AFTER = re.compile(
    rf"(?:^\s*/\s*(?:\d+\s*)?{_RATE_UNIT}|^\s*/\s*\d+\s*(?:mes(?:es)?|m[åa]n\w*|months?)"
    rf"|(?<![^\W\d_])(?:per|pr\.?|a\s+la|al|a|por|each|every|cada|om|la)\s+{_RATE_UNIT}"
    rf"|(?<![^\W\d_])i\s+veckan"
    rf"|(?<![^\W\d_])(?:cada|every|var)\s+\d+\s*(?::e\s+)?(?:mes(?:es)?|m[åa]nad\w*|months?"
    rf"|semanas|veckor|weeks|d[ií]as|dagar|days))(?![^\W\d_])",
    re.IGNORECASE,
)
_RATE_WORD = re.compile(
    r"(?<![^\W\d_])(?:semanal\w*|diari[oa]s?|semestral\w*|bimestral\w*|trimestral\w*"
    r"|quincenal\w*|halv[åa]rs\w*|kvartals\w*|vecko\w*|weekly|daily|biweekly|fortnightly"
    r"|semi-?annual\w*|half-yearly|quarterly)(?![^\W\d_])",
    re.IGNORECASE,
)
_QUARTER_RATE = re.compile(r"trimestr|kvartal|quarter", re.IGNORECASE)
# An annual or instalment plan stated next to a monthly amount.
_ANNUAL = re.compile(
    r"(?<![^\W\d_])(?:anual\w*|annual\w*|yearly|årsbetalning\w*|årsvis|årlig\w*"
    r"|årsabonnemang\w*|helårs\w*|equivale\w*|equivalent\w*|\d+\s*x\s+de|\d+\s+cuotas"
    r"|\d+\s+parcelas|\d+\s*x(?=\s*(?:r\$|\$|us\$))|en\s+\d+\s+cuotas|delbetalning\w*"
    r"|a[ñn]o\s+completo|ano\s+inteiro|full\s+year|hela\s+[åa]ret)"
    r"(?![^\W\d_])",
    re.IGNORECASE,
)
# Two amounts of a range ("$ 21.900 - $ 29.900", "entre $ 21.900 y $ 29.900") or of a list
# ("199/295 kr/mån"): offers of no one price.
_RANGE_DASH = re.compile(r"^\s*(?:/\s*[^\s\d/]{1,8}\.?)?\s*[-–—]\s*$")
_RANGE_JOIN = re.compile(
    r"^\s*(?:/\s*[^\s\d/]{1,8}\.?)?\s*(?:y|e|a|and|to|och|till|hasta|até)\s*$", re.IGNORECASE
)
_RANGE_LEAD = re.compile(r"(?<![^\W\d_])(?:entre|between|mellan|from|desde)\s*$", re.IGNORECASE)
_LIST_BEFORE = re.compile(r"\d\s*/\s*$")
# A struck-through price whose markup was lost: only blanks, a period or a badge between it and
# a lower price ("$ 24.900" / "$ 21.900/mes", "339 kr 295 kr/mån", "$ 24.900 12% OFF $ 21.900").
_ADJACENT_BETWEEN = re.compile(
    r"^\s*(?:/\s*[^\s\d/]{1,8}\.?)?\s*(?:\(?\s*[-–]?\s*\d{1,3}\s*%\s*(?:off|dto\.?|de\s+descuento"
    r"|desconto|rabatt)?\s*\)?\s*)?$",
    re.IGNORECASE,
)

_LETTER = re.compile(r"[^\W\d_]")

# An ISO code written right after (or before) an ambiguous "$" amount: "$ 399 MXN", "MXN $399",
# "(USD): $ 19.99"; the nationality after "pesos" ("19900 pesos mexicanos", "$ 399 pesos
# mexicanos"); "dólares" after the amount.
_CODE_AFTER = re.compile(r"^\s*(?:/\s*[^\W\d_]{1,8}\.?\s*)?\(?([A-Z]{3})(?![A-Za-z])")
_CODE_BEFORE = re.compile(r"(?<![A-Za-z])\(?([A-Z]{3})\)?\s*:?\s*$")
_PESOS_OF = re.compile(
    r"^\s*(?:pesos?\s+)?(mexicanos|colombianos|argentinos|chilenos)\b", re.IGNORECASE
)
_PESOS_CODES = {"mexicanos": "MXN", "colombianos": "COP", "argentinos": "ARS", "chilenos": "CLP"}
_DOLLARS_AFTER = re.compile(
    r"^\s*(?:/\s*[^\W\d_]{1,8}\.?\s*)?\(?\s*(?:d[oó]lares|dollars?)(?![^\W\d_])", re.IGNORECASE
)
_BARE_DOLLAR = re.compile(r"(?<![A-Za-z])\$")
_BARE_KRONA = re.compile(r"(?<![^\W\d_])kr\.?$", re.IGNORECASE)
_KRONOR_OF = re.compile(
    r"^\s*(?:/\s*[^\W\d_]{1,8}\.?\s*)?\(?\s*(danske?|norske?|norska|danska|svenska|isl[äa]ndska)"
    r"\s+kron\w+",
    re.IGNORECASE,
)
_KRONOR_CODES = {"dans": "DKK", "nors": "NOK", "sven": "SEK", "isla": "ISK", "islä": "ISK"}
_CURRENCY_WORD = re.compile(r"(?:pesos?|kronor|krona|reais|soles)$", re.IGNORECASE)
# A currency line of the text: "Precios en dólares (USD)", "Todos los precios en USD".
_CURRENCY_LINE = re.compile(
    r"(?<![^\W\d_])(?:precios?|pre[çc]os?|valores|tarifas|prices?|priser|montos|importes)"
    r"(?:\s+[^\W\d_]+){0,3}?\s+(?:en|in|em|i)\s+(?:(?P<word>d[oó]lares|dollars?|pesos\s+"
    r"(?:mexicanos|colombianos|argentinos|chilenos)|reais|euros?|kronor)|\(?(?-i:(?P<code>[A-Z]{3}"
    r"))\)?)(?![^\W\d_])",
    re.IGNORECASE,
)

# A name inherited by another tier ("todos los beneficios de Meli+ Esencial", folded text).
_INHERITED = re.compile(
    r"(?:beneficios|ventajas|vantagens|formaner|fordelar|benefits|perks|everything|todo\s+lo"
    r"|tudo|allt)\s+(?:(?:de|del|do|da|dos|das|of|in|i|fran|med)\s+)?"
    r"(?:(?:los|las|el|la|o|a|the)\s+)?(?:(?:planes?|plano|abonnemanget)\s+)?$"
)
# The same lead-in anywhere ("Meli+ Total: todo lo de Meli+ Esencial, que cuesta $ 9.900").
_INHERITED_ANY = re.compile(
    r"(?<![^\W\d_])(?:beneficios|ventajas|vantagens|formaner|fordelar|benefits|perks|everything"
    r"|todo\s+lo|tudo|allt)\s+(?:de|del|do|da|dos|das|of|in|i|fran|med)(?![^\W\d_])"
)
_SENTENCE_OR_LINE = re.compile(r"\n|[!?;•|]|\.(?=\s|$|[A-ZÀ-ÖØ-Þ])")
# A name compared with ("5 veces más altos que los de AI Pro", "than Individual", folded).
_COMPARED = re.compile(
    r"(?<![^\W\d_])(?:que\s+(?:(?:los|las|el|la|lo|o|a|os|as)\s+)?(?:de|del|do|da|dos|das|en)"
    r"|que\s+(?:o|a|os|as|el|la)|do\s+que|da\s+que|than|(?:mer|mindre|hogre|lagre|battre"
    r"|storre)\s+an|compared\s+(?:to|with)|comparad[oa]s?\s+(?:con|com|a)"
    r"|en\s+comparacion\s+con|em\s+comparacao\s+com|jamfort\s+med|vs\.?|versus|frente\s+a"
    r"|upgrade\s+from)\s+(?:(?:el|la|los|las|the|o|a|os|as|plan|plano|planes|planos"
    r"|abonnemanget|nivel)\s+)*$"
)


Role = Literal["regular", "intro", "compare", "savings", "offer", "tax", "per_person"]
_ROLE_NAMES: dict[str, str] = {
    "regular": "the regular price",
    "intro": "an introductory price",
    "compare": "a comparison (struck-through) price",
    "savings": "an amount saved",
    "offer": "an offer price the quote ties to no duration of its own",
    "tax": "a tax amount",
    "per_person": "a price per person or per extra member",
}


@dataclass(frozen=True)
class _Price:
    info: PriceInfo
    role: Role
    currency: str | None
    # A previous / ordinary price made regular: the index of the intro price next to it.
    partner: int | None = None
    label: str | None = None  # "previous" / "ordinary"
    then: bool = False  # right after "then / luego / därefter ..."
    ex_tax: bool = False  # stated before tax
    rate: str | None = None  # a rate the period markers do not read ("a la semana")
    annual: bool = False  # an annual or instalment plan stated next to it
    by_position: bool = False  # struck-through by position alone (see _ADJACENT_BETWEEN)

    @property
    def start(self) -> int:
        return self.info.money.start

    @property
    def end(self) -> int:
        return self.info.money.end

    @property
    def amount(self) -> Decimal:
        return self.info.money.amount


def _money_value(
    value: object, currency: str, period: str | None
) -> tuple[Decimal, str, str | None] | None:
    if isinstance(value, str) and len(value) <= 60 and (parsed := parse_money_code(value)):
        amount, cur, per = parsed
        if (bounded := _bounded(amount)) is None:
            return None
        return bounded, cur, per or period
    amount = _decimal(value)
    if amount is None:
        return None
    return amount, currency, period


def _context_text(quote: str, context_lines: Sequence[str]) -> tuple[str, tuple[int, int]]:
    """The text to classify and the span of the quote inside it. The context lines are joined
    one per line; a quote that cannot be found in them is checked on its own."""
    quote = unicodedata.normalize("NFC", quote)
    if context_lines:
        text = unicodedata.normalize("NFC", "\n".join(context_lines))
        if len(text) <= MAX_CHECK_CHARS:
            folded, index = _fold_map(text)
            needle = _fold(quote)
            at = folded.find(needle) if needle else -1
            if at >= 0:
                return text, (index[at], index[at + len(needle) - 1] + 1)
        else:
            return text, (0, 0)
    return quote, (0, len(quote))


def _currency_lines(text: str) -> set[str]:
    """The currencies the currency lines of `text` declare ("Precios en dólares (USD)")."""
    found: set[str] = set()
    for m in _CURRENCY_LINE.finditer(text):
        if code := m.group("code"):
            if code in ISO_CODES:
                found.add(code)
            continue
        word = m.group("word").lower()
        if word.startswith(("d", "dollar")):
            found.add("USD")
        elif word.startswith("pesos"):
            found.add(_PESOS_CODES[word.split()[-1]])
        elif word == "reais":
            found.add("BRL")
        elif word.startswith("euro"):
            found.add("EUR")
        elif word == "kronor":
            found.add("SEK")
    return found


def _explicit_currency(text: str, info: PriceInfo, declared: set[str]) -> str | None:
    """The currency written next to an amount whose marker is ambiguous (a bare "$" or a currency
    word): an ISO code ("$ 399 MXN", "MXN $399", "(USD): $ 19.99"), "pesos mexicanos",
    "dólares", or the currency lines of the text ("?" when they declare several)."""
    raw, start, end = info.money.raw, info.money.start, info.money.end
    after = text[end : end + 30]
    if _BARE_KRONA.search(raw):
        if m := _KRONOR_OF.match(after):
            return _KRONOR_CODES[m.group(1).lower()[:4]]
        m = _CODE_AFTER.match(after)
        return m.group(1) if m and m.group(1) in ISO_CODES else None
    if not (_BARE_DOLLAR.search(raw) or _CURRENCY_WORD.search(raw)):
        return None
    if m := _PESOS_OF.match(after):
        return _PESOS_CODES[m.group(1).lower()]
    if _DOLLARS_AFTER.match(after):
        return "USD"
    for m in (_CODE_AFTER.match(after), _CODE_BEFORE.search(text[max(0, start - 10) : start])):
        if m and m.group(1) in ISO_CODES:
            return m.group(1)
    if len(declared) == 1:
        return next(iter(declared))
    if declared:
        return "?"
    return None


def _offer_word(before: str) -> bool:
    """An offer word in the last clause of the price's own line, not about shipping or ads."""
    clause = _SENTENCE.split(before.rsplit("\n", 1)[-1])[-1]
    return _has_offer_word(_OFFER_WORD, clause)


def _has_offer_word(rx: re.Pattern[str], text: str) -> bool:
    return any(
        not _NOT_OFFER_BEFORE.search(text[: m.start()])
        and not _NOT_OFFER_AFTER.match(text[m.end() :])
        for m in rx.finditer(text)
    )


@dataclass(frozen=True)
class _Around:
    """The text around one price: since the previous price (`before`), up to the next one
    (`after`), and the price's own sentence on its line on either side."""

    before: str
    after: str
    sentence_before: str
    sentence_after: str
    lines_above: str  # up to two lines before the price's line


def _around(text: str, start: int, end: int, before: str, after: str) -> _Around:
    line_start = text.rfind("\n", 0, start) + 1
    own_before = text[line_start:start]
    cuts = [m.end() for m in _SENTENCE_END.finditer(own_before)]
    sentence_before = own_before[cuts[-1] :] if cuts else own_before
    line_after = after.split("\n", 1)[0]
    cut = _SENTENCE_END.search(line_after)
    sentence_after = line_after[: cut.start()] if cut else line_after
    above_start = line_start
    for _ in range(2):
        if above_start <= 0:
            break
        above_start = text.rfind("\n", 0, above_start - 1) + 1
    return _Around(before, after, sentence_before, sentence_after, text[above_start:line_start])


def _savings(a: _Around) -> bool:
    if is_savings(a.before, a.after) or _V_SAVINGS_AFTER.match(a.sentence_after):
        return True
    if _SAVE_LIST_BEFORE.search(a.sentence_before):
        return True
    m = _V_SAVINGS_BEFORE.search(a.sentence_before[-60:])
    if m is None:
        return False
    lead = a.sentence_before[-60:][: m.start()]
    return not _PRICE_LEAD.search(lead[-40:])


def _tax_amount(a: _Around) -> bool:
    tail = a.sentence_before[-40:]
    if _TAX_PAREN.search(tail):
        return True
    if _EX_TAX_BEFORE.search(tail):
        return False  # "sin IVA", "exkl. moms:": a price before tax (_ex_tax)
    m = _TAX_LABEL.search(tail)
    return m is not None and not _TAX_INCLUDED_LEAD.search(tail[: m.start()])


def _base_role(text: str, start: int, a: _Around) -> Role:
    """The role of the price at `start` (module docstring, "Roles")."""
    before, after = a.before, a.after
    if _savings(a):
        return "savings"
    if _tax_amount(a):
        return "tax"
    if (
        is_comparison(before, after)
        or _V_COMPARE_BEFORE.search(a.sentence_before[-60:])
        or _V_COMPARE_AFTER.match(a.sentence_after)
    ):
        return "compare"
    if _PER_PERSON_AFTER.match(a.sentence_after) or _PER_PERSON_BEFORE.search(
        a.sentence_before[-60:]
    ):
        return "per_person"
    then = follows_then(before)

    def timed(tail: str) -> bool:
        """A duration right after the price, in `tail`."""
        return is_intro(before, tail) or (not then and bool(_INTRO_TAIL.match(tail)))

    line_after = after.split("\n", 1)[0]
    cut = _SENTENCE_END.search(line_after)
    if (
        timed(a.sentence_after)
        or is_intro(text[:start], "")
        or (not then and _INTRO_LEAD.search(before[-80:]))
    ):
        return "intro"
    if cut and timed(line_after) and _BACK_REFERENCE.match(line_after[cut.end() :]):
        return "intro"  # "199 kr/mån.Rabatten gäller i 6 månader"
    if timed(after):
        # A duration in the next sentence or on the next line may be another offer's ("295
        # kr/mån. Prova gratis i 1 månad"): neither regular nor intro.
        return "offer"
    if _LIST_BEFORE.search(a.sentence_before):
        return "offer"  # "199/295 kr/mån"
    if not then and (
        _offer_word(before)
        or _has_offer_word(_OFFER_WORD_AFTER, a.sentence_after)
        or _FROM_BEFORE.search(a.sentence_before[-40:])
        or _UNTIL_DATE.search(_fold(a.sentence_after))
    ):
        return "offer"
    return "regular"


def _rate(a: _Around) -> str | None:
    """A rate of the price that the period markers do not read, as written."""
    m = _RATE_AFTER.search(a.sentence_after[:40]) or _RATE_WORD.search(a.sentence_after[:40])
    m = m or _RATE_WORD.search(a.sentence_before[-30:])
    if m is None:
        return None
    return "quarter" if _QUARTER_RATE.search(m.group()) else " ".join(m.group().split())


def _annual(a: _Around) -> bool:
    return bool(
        _ANNUAL.search(a.sentence_before)
        or _ANNUAL.search(a.sentence_after)
        or _ANNUAL.search(a.lines_above)
    )


def _ex_tax(a: _Around) -> bool:
    line_after = a.after.split("\n", 1)[0]  # "excl. VAT" is no sentence end
    return bool(_EX_TAX_AFTER.match(line_after) or _EX_TAX_BEFORE.search(a.sentence_before[-40:]))


def _label(text: str, prices: Sequence[_Price], k: int, before: str) -> str | None:
    """Whether a label marks the k-th price as the "previous" or the "ordinary" one (module
    docstring, "Decision"); None when none does."""
    if _PREVIOUS_BEFORE.search(before[-60:]):
        return "previous"
    if _ORDINARY_BEFORE.search(before[-60:]):
        return "ordinary"
    if _DE_BEFORE.search(before[-20:]) and k + 1 < len(prices):
        p, nxt = prices[k].info.money, prices[k + 1].info.money
        if _DE_BETWEEN.match(text[p.end : nxt.start]) and nxt.amount < p.amount:
            return "previous"
    return None


def _same_period(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b


def _prices(text: str, currency: str) -> list[_Price]:
    """Every price of `text` with its role (module docstring, "Roles" and "Decision") and its
    currency (a currency written next to a "$" amount wins)."""
    infos = [
        i
        for i in classify_prices(text, [currency], currency_words=True)
        if _bounded(i.money.amount) is not None
    ]
    declared = _currency_lines(text)
    base: list[_Price] = []
    befores: list[str] = []
    sentences: list[str] = []
    prev_end = 0
    for k, info in enumerate(infos):
        start, end = info.money.start, info.money.end
        nxt = infos[k + 1].money.start if k + 1 < len(infos) else len(text)
        a = _around(text, start, end, text[prev_end:start], text[end:nxt])
        cur = _explicit_currency(text, info, declared) or info.money.currency
        base.append(
            _Price(
                info,
                _base_role(text, start, a),
                cur,
                then=follows_then(a.before),
                ex_tax=_ex_tax(a),
                rate=_rate(a),
                annual=_annual(a),
            )
        )
        befores.append(a.before)
        sentences.append(a.sentence_before)
        prev_end = end
    labels = [_label(text, base, k, befores[k]) for k in range(len(base))]
    base = [replace(p, label=labels[k]) for k, p in enumerate(base)]
    lines = [text.count("\n", 0, p.start) for p in base]
    # The amounts of a range.
    for k in range(len(base) - 1):
        p, q = base[k], base[k + 1]
        between = text[p.end : q.start]
        if q.currency == p.currency and (
            _RANGE_DASH.match(between)
            or (_RANGE_JOIN.match(between) and _RANGE_LEAD.search(sentences[k]))
        ):
            for j in (k, k + 1):
                if base[j].role == "regular" and labels[j] is None:
                    base[j] = replace(base[j], role="offer")
    # A struck-through price whose markup was lost.
    for k in range(len(base) - 1):
        p, q = base[k], base[k + 1]
        if (
            labels[k] is None
            and p.role in ("regular", "offer")
            and q.currency == p.currency
            and q.amount < p.amount
            and _same_period(p.info.period, q.info.period)
            and _ADJACENT_BETWEEN.match(text[p.end : q.start])
        ):
            base[k] = replace(p, role="compare", by_position=True)
    out: list[_Price] = []
    for k, p in enumerate(base):
        if labels[k] is None or p.role not in ("regular", "compare"):
            out.append(p)
            continue
        amount = p.amount
        others = [j for j, q in enumerate(base) if j != k and q.currency == p.currency]
        line = [j for j in others if lines[j] == lines[k]]
        intro_next_to_it = next(
            (
                j
                for j in (k - 1, k + 1)
                if j in line and base[j].role == "intro" and base[j].amount < amount
            ),
            None,
        )
        competing = any(base[j].role == "regular" and labels[j] is None for j in line)
        lower = any(
            base[j].amount < amount and _same_period(base[j].info.period, p.info.period)
            for j in others
        )
        if intro_next_to_it is not None and not competing:
            # The price that follows a timed offer.
            out.append(replace(p, role="regular", partner=intro_next_to_it))
        elif labels[k] == "ordinary" and not lower and not is_comparison("", text[p.end :]):
            out.append(replace(p, role="regular"))  # nothing cheaper around
        else:
            out.append(replace(p, role="compare"))
    # A lower price around an ordinary one is a discounted price.
    for k, p in enumerate(out):
        if p.label != "ordinary" or p.partner is not None:
            continue
        for j, q in enumerate(out):
            if (
                j != k
                and abs(lines[j] - lines[k]) <= 1
                and q.currency == p.currency
                and q.role == "regular"
                and q.label is None
                and q.amount < p.amount
                and _same_period(q.info.period, p.info.period)
            ):
                out[j] = replace(q, role="offer")
    # The amount of an introductory offer, restated without its duration.
    for j, q in enumerate(out):
        if (
            q.role == "regular"
            and q.label is None
            and any(
                i != j and r.role == "intro" and r.currency == q.currency and r.amount == q.amount
                for i, r in enumerate(out)
            )
        ):
            out[j] = replace(q, role="offer")
    return out


def _describe(prices: Sequence[_Price]) -> str:
    return ", ".join(
        f"{p.amount} {p.currency or '?'}{'/' + p.info.period if p.info.period else ''} {p.role}"
        for p in prices
    )


# ---- money: tiers ---------------------------------------------------------------------------


def _tier_names(b: Bundle, kind: ValueKind) -> list[str]:
    if kind == "base_money" and b.price.base_plan_name:
        return [b.price.base_plan_name]
    return list(dict.fromkeys([*b.aliases, b.name]))


def _other_tier_names(
    bundle: Bundle, kind: ValueKind, siblings: Sequence[Bundle]
) -> list[tuple[str, str]]:
    """(tier, name) of every name that is NOT the bundle's for `kind`: the siblings' names and
    base plans, the bundle's base plan (money), the bundle's own names (base_money)."""
    out: list[tuple[str, str]] = []
    if kind == "base_money" and bundle.price.base_plan_name:
        out.extend((bundle.bundle_id, n) for n in [*bundle.aliases, bundle.name])
    elif bundle.price.base_plan_name:
        out.append((f"the base plan of {bundle.bundle_id}", bundle.price.base_plan_name))
    for s in siblings:
        out.extend((s.bundle_id, n) for n in [*s.aliases, s.name])
        if s.price.base_plan_name:
            out.append((f"the base plan of {s.bundle_id}", s.price.base_plan_name))
    return out


@dataclass(frozen=True)
class _Mention:
    start: int
    end: int
    tier: str
    printed: str
    lead: Literal["inherited", "compared"] | None


_OWN = "\x00own"


@dataclass(frozen=True)
class _Rivals:
    """The other prices of the text that keep an amount from a tier name (module docstring,
    the tier rule): `blockers` (sorted offsets) may not lie between a tier name and the amount,
    nor in the amount's clause when it names no tier. Under a card heading they separate the
    heading from the amount too, but for `own_struck`: the struck price of an introductory
    amount's own offer. `struck` (struck through by position alone) and `intro` word the
    rejection."""

    blockers: Sequence[int] = ()
    struck: Sequence[int] = ()
    intro: bool = False
    own_struck: frozenset[int] = frozenset()

    @property
    def card_blockers(self) -> Sequence[int]:
        return [b for b in self.blockers if b not in self.own_struck]


@dataclass
class _Tiers:
    """The tier names of a text for one bundle and kind (module docstring, the tier rule)."""

    text: str
    own_names: list[str]
    clauses: _Clauses
    by_clause: dict[int, list[_Mention]] = field(default_factory=dict)
    any_mention: bool = False
    # The offsets where the text's lines start, the mentions of each line, and the lines that
    # head a card (empty without the text's amounts: no card rule).
    line_starts: list[int] = field(default_factory=lambda: [0])
    by_line: dict[int, list[_Mention]] = field(default_factory=dict)
    headings: set[int] = field(default_factory=set)

    @classmethod
    def of(
        cls,
        text: str,
        kind: ValueKind,
        bundle: Bundle,
        siblings: Sequence[Bundle],
        amounts: Sequence[int] | None = None,
    ) -> _Tiers:
        """`amounts` are the offsets of every amount of the text (any currency, any role); the
        card rule needs them to tell a heading from a price line, and is off without them."""
        own = _tier_names(bundle, kind)
        owner: dict[str, tuple[str, str]] = {}
        for n in own:
            owner.setdefault(_fold(n), (_OWN, n))
        for tier, n in _other_tier_names(bundle, kind, siblings):
            owner.setdefault(_fold(n), (tier, n))
        folded, index = _fold_map(text)
        tiers = cls(text, own, _Clauses.of(text))
        tiers.line_starts = [0, *(m.end() for m in re.finditer("\n", text))]
        for s, e, name in _mentions(folded, owner):
            window = folded[max(0, s - 60) : s]
            lead: Literal["inherited", "compared"] | None = None
            if _COMPARED.search(window):
                lead = "compared"
            elif _INHERITED.search(window):
                lead = "inherited"
            tier, printed = owner[name]
            m = _Mention(index[s], index[e - 1] + 1, tier, printed, lead)
            tiers.by_clause.setdefault(tiers.clauses.of_pos(m.start), []).append(m)
            tiers.by_line.setdefault(tiers._line_of(m.start), []).append(m)
            tiers.any_mention = True
        if amounts is not None:
            priced = {tiers._line_of(a) for a in amounts}
            tiers.headings = {
                i
                for i, ms in tiers.by_line.items()
                if i not in priced
                and all(m.lead is None for m in ms)
                and len({m.tier for m in ms}) == 1
            }
        return tiers

    def _line_of(self, pos: int) -> int:
        return bisect.bisect_right(self.line_starts, pos) - 1

    def _card(self, start: int) -> int | None:
        """The heading line of the card the amount at `start` lies in, or None: the nearest
        line above the amount's that names a tier, when it is a heading and the amount's own
        line names none."""
        line = self._line_of(start)
        if line in self.by_line:
            return None
        for j in range(line - 1, -1, -1):
            if j in self.by_line:
                return j if j in self.headings else None
        return None

    def named(self, start: int, end: int, rivals: _Rivals | None = None) -> list[_Mention] | str:
        """The mentions that name the tier of what spans start-end (an amount, a service), or
        why none does (see _Rivals)."""
        rivals = rivals or _Rivals()
        blockers = rivals.blockers

        def blocked(a: int, b: int, offsets: Sequence[int] = blockers) -> bool:
            i = bisect.bisect_left(offsets, a)
            return (i < len(offsets) and offsets[i] < b) or self._inherited_between(a, b)

        k = self.clauses.of_pos(start)
        found: list[_Mention] = []
        for m in self.by_clause.get(k, []):
            if m.lead == "compared":
                continue
            if m.end <= start and blocked(m.end, start):
                continue
            if m.start >= end and blocked(end, m.start):
                continue
            found.append(m)
        heading = None if found else self._card(start)
        # Under a card heading, the struck price of an introductory amount's own offer is no
        # other price ("$ 21.900 9% OFF $ 19.900/mes por 2 meses").
        exempt = rivals.own_struck if heading is not None else frozenset()
        cs, ce = self.clauses.span(k)
        if not found and any(cs <= b < ce and b not in exempt for b in blockers):
            return (
                "the amount's clause states another price and names no tier: an earlier "
                "heading is not the amount's"
            )
        expected = f"expected one of {self.own_names}"
        if heading is not None:
            ms = self.by_line[heading]
            card = rivals.card_blockers
            found = [m for m in ms if not blocked(m.end, start, card)]
            if not found:
                i = bisect.bisect_left(card, ms[-1].end)
                if not (i < len(card) and card[i] < start):
                    why = "its sentence names what another tier inherits"
                elif card[i] in rivals.struck and not rivals.intro:
                    why = (
                        "a price struck through by position alone precedes it, and the amount "
                        "is not introductory"
                    )
                else:
                    why = "another price lies between them"
                return (
                    f"the quote names no tier for the amount (under the card heading "
                    f"{ms[0].printed!r}, {why}; {expected})"
                )
        if not found:
            for j in range(k - 1, -1, -1):
                ms = [m for m in self.by_clause.get(j, []) if m.lead is None]
                if ms:
                    found = [m for m in ms if not blocked(m.end, start)]
                    break
        if found:
            return found
        if not self.any_mention:
            return f"the quote names no tier ({expected})"
        return (
            "the quote names no tier for the amount (in or before its clause, with no other "
            f"price in between; {expected})"
        )

    def _inherited_between(self, a: int, b: int) -> bool:
        """A name inherited by another tier ("todo lo de", "everything in") in the amount's
        sentence, between a tier name at `a` and the amount at `b`."""
        part = self.text[a:b]
        cut = max((m.end() for m in _SENTENCE_OR_LINE.finditer(part)), default=0)
        return bool(_INHERITED_ANY.search(_fold(part[cut:])))

    def error(self, start: int, end: int, rivals: _Rivals | None = None) -> str | None:
        """None when the tier named for start-end is the bundle's own."""
        found = self.named(start, end, rivals)
        if isinstance(found, str):
            return found
        tiers = {m.tier: m.printed for m in found}
        if len(tiers) > 1:
            names = ", ".join(sorted(tiers.values()))
            return f"the clause that names the amount's tier names several ({names})"
        ((tier, printed),) = tiers.items()
        if tier == _OWN:
            return None
        return f"the tier named next to the amount is {printed!r} ({tier})"

    def other_tier(self, start: int, end: int, rivals: _Rivals | None = None) -> bool:
        """True only when start-end is known to be another tier's."""
        found = self.named(start, end, rivals)
        return not isinstance(found, str) and all(m.tier != _OWN for m in found)


def _blocks(p: _Price) -> bool:
    """A price that a tier name and its amount may not have between them."""
    return (p.role in ("regular", "offer") and p.label is None) or p.by_position


# ---- money: check ---------------------------------------------------------------------------


def _plausibility(
    amount: Decimal, currency: str, period: str | None, kind: ValueKind, fx: FxTable | None
) -> tuple[ValueCheck | None, str]:
    if fx is None:
        return None, "plausibility not checked (no FX table)"
    try:
        monthly = amount / _MONTHS_PER.get(period or "month", 1)
        eur = fx.to_eur(monthly, currency)
    except (InvalidOperation, Overflow, DivisionByZero):
        return None, "plausibility not checked (the amount cannot be converted)"
    if eur is None:
        return None, f"plausibility not checked (no FX rate for {currency})"
    lo, hi = PLAUSIBLE_MONTHLY_EUR
    if kind == "intro_money":
        lo = Decimal(0)  # an introductory offer may be free
    if not lo <= eur <= hi:
        return (
            ValueCheck(
                "implausible",
                f"{format_money_code(amount, currency, period)} is {eur} EUR/month "
                f"(plausible: {lo}-{hi})",
            ),
            "",
        )
    return None, f"{eur} EUR/month"


def _big_change(
    amount: Decimal, period: str | None, current: object, currency: str, current_period: str | None
) -> tuple[bool, str]:
    """Whether the monthly equivalent changes by more than BIG_CHANGE against `current`, and a
    note when `current` cannot be compared."""
    if current is None:
        return False, ""
    parsed = _money_value(current, currency, current_period)
    if parsed is None:
        return False, f"; change not measured: current value {str(current)[:40]!r} is not an amount"
    old, old_currency, old_period = parsed
    if old_currency != currency:
        return False, f"; change not measured: current value {current!r} is not in {currency}"
    try:
        new_m = amount / _MONTHS_PER.get(period or "month", 1)
        old_m = old / _MONTHS_PER.get(old_period or "month", 1)
        if old_m == 0:
            return new_m != 0, ""
        return abs(new_m - old_m) / abs(old_m) > BIG_CHANGE, ""
    except (InvalidOperation, Overflow, DivisionByZero):
        return False, "; change not measured"


def big_change(
    amount: Decimal,
    period: str | None,
    current: object,
    currency: str,
    current_period: str | None = None,
) -> bool:
    """Whether `amount` per `period` is more than BIG_CHANGE away from `current` (the YAML
    value, per `current_period`) in monthly terms; False when they cannot be compared. The
    review side (haggler.bundles.proposals) measures a stored finding against today's YAML."""
    return _big_change(amount, period, current, currency, current_period)[0]


def _check_money(
    kind: ValueKind,
    value: object,
    quote: str,
    *,
    context_lines: Sequence[str],
    country: str,
    currency: str,
    period: str | None,
    provider: Provider | None,
    bundle: Bundle | None,
    bundles: Iterable[Bundle] | None,
    fx: FxTable | None,
    current: object,
    url: str | None,
) -> ValueCheck:
    parsed = _money_value(value, currency, period)
    if parsed is None:
        return _missing(f"value {str(value)[:40]!r} is not an amount like '21900 COP/month'")
    amount, cur, per = parsed
    if cur != currency:
        return _missing(f"value currency {cur} is not the bundle's {currency}")
    if url is not None and provider is not None:
        from haggler.catalog import on_domains

        if not on_domains(url, provider.domains.get(country, [])):
            return _missing(f"page {url} is not on a {country} domain of provider {provider.id}")
    text, (qs, qe) = _context_text(quote, context_lines)
    if len(text) > MAX_CHECK_CHARS:
        return _too_long(len(text))
    prices = _prices(text, currency)
    in_quote = [p for p in prices if qs <= p.start and p.end <= qe]
    if not in_quote:
        return _missing(f"the quote states no price in {currency}")
    same = [p for p in in_quote if p.amount == amount]
    if not same:
        return _missing(
            f"the quote does not state {format_money_code(amount, currency)} "
            f"(it states: {_describe(in_quote)})"
        )
    want = "intro" if kind == "intro_money" else "regular"
    tiers: _Tiers | None = None
    if bundle is not None:
        if bundles is None:
            from haggler.catalog import default_bundles

            bundles = default_bundles().bundles
        siblings = [
            b
            for b in bundles
            if b.provider_id == bundle.provider_id
            and b.country == country
            and b.bundle_id != bundle.bundle_id
        ]
        tiers = _Tiers.of(text, kind, bundle, siblings, [q.start for q in prices])
    tax_included = bundle.price.tax_included if bundle is not None else None

    def own_struck(target: _Price) -> frozenset[int]:
        """The struck prices of an intro's own offer: struck through by position by an intro of
        its amount right after it, with no word before them on their line ("$ 21.900 9% OFF" /
        "$ 19.900/mes por 2 meses"; not "Agrega Disney+ por $ 8.000/mes", "desde $ 30.000")."""
        if target.role != "intro":
            return frozenset()
        return frozenset(
            q.start
            for q, nxt in itertools.pairwise(prices)
            if q.by_position
            and nxt.role == "intro"
            and nxt.amount == target.amount
            and not _LETTER.search(text, text.rfind("\n", 0, q.start) + 1, q.start)
        )

    def rivals(target: _Price) -> _Rivals:
        others = [q for q in prices if q.currency == currency and q.amount != target.amount]
        return _Rivals(
            sorted(q.start for q in others if _blocks(q)),
            sorted(q.start for q in others if q.by_position),
            target.role == "intro",
            own_struck(target),
        )

    def reject(p: _Price) -> str | None:
        raw = repr(p.info.money.raw)
        if p.currency != currency:
            known = p.currency not in (None, "?")
            return f"{raw} is {p.currency if known else 'an unknown currency'}"
        if p.role != want:
            return f"{raw} is {_ROLE_NAMES[p.role]}, not {_ROLE_NAMES[want]}"
        if per is not None and p.info.period is not None and p.info.period != per:
            return f"{raw} is per {p.info.period}, not per {per}"
        if p.rate is not None and p.rate != per:
            return f"{raw} is billed {p.rate!r}, not per {per or 'month'}"
        if p.annual and (per or "month") == "month":
            return f"{raw} is the monthly equivalent of an annual or instalment plan"
        if p.ex_tax and tax_included is not False:
            return f"{raw} is a price before tax; the bundle's price includes it"
        if tiers is None:
            return None
        if err := tiers.error(p.start, p.end, rivals(p)):
            return err
        if p.partner is not None:
            intro = prices[p.partner]
            if err := tiers.error(intro.start, intro.end, rivals(intro)):
                # A previous price is the regular one only after its own tier's offer.
                return f"{raw} follows another tier's offer: {err}"
        return None

    def later_price(p: _Price) -> _Price | None:
        """A price stated as the one paid after the offer that `p` (a previous price made
        regular) follows (module docstring, "Decision")."""
        for j, q in enumerate(prices):
            if q is p or j == p.partner or q.currency != currency or q.amount == p.amount:
                continue
            if not (
                (q.then and q.role in ("regular", "offer"))
                or (q.role == "regular" and q.label is None)
            ):
                continue
            if tiers is None or not tiers.other_tier(q.start, q.end, rivals(q)):
                return q
        return None

    why: list[str] = []
    for p in same:
        reason = reject(p)
        if reason is None and p.partner is not None and (q := later_price(p)) is not None:
            reason = (
                f"{p.info.money.raw!r} is a previous price, but {q.info.money.raw!r} is "
                "stated as the price after the offer"
            )
        if reason is not None:
            if reason not in why:
                why.append(reason)
            continue
        implausible, note = _plausibility(amount, currency, per, kind, fx)
        if implausible is not None:
            return implausible
        big, change_note = _big_change(amount, per, current, currency, period)
        return _ok(f"{p.info.money.raw!r} {p.role}; {note}{change_note}", big_change=big)
    return _missing("; ".join(why))


# ---- components -----------------------------------------------------------------------------


# Single-word aliases matched as written (or upper case, or capitalized): short ones and common
# words ("Max" is HBO Max, "max 5 personer" is not).
def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_COMMON_WORD_ALIASES = _words("max plus one go play music tv now")
_ALIAS_BEFORE_NUMBER = re.compile(r"\.?\s*\d")
_NEGATED = re.compile(
    r"(?<![^\W\d_])(?:(?:no|nao|not|inte|ej|ikke|never|nunca)\s+(?:(?:esta|estan|es|is|are|e"
    r"|sao|ar|esta\s+incluido\s+en)\s+)?(?:incluid\w*|inclus\w*|includ\w*|ingar\w*"
    r"|disponible\w*|available|tillganglig\w*)|ingar\s+(?:inte|ej)|(?:no|nao)\s+(?:incluye"
    r"|inclui|tiene|tem|trae|traz)|does\s+not\s+include|doesn'?t\s+include|excluid\w*"
    r"|excluded|exceto|excepto|except|utom|forutom)(?![^\W\d_])"
)
_WITHOUT_BEFORE = re.compile(
    r"(?<![^\W\d_])(?:sin|sem|utan|without|ni|nor)(?:\s+(?:el|la|los|las|o|a|os|as|the))?\s*$"
)
_WITHOUT_HEAD = re.compile(r"^\W*(?:sin|sem|utan|without)(?![^\W\d_])")
_DISCOUNT_MARK = re.compile(
    r"%|(?<![^\W\d_])(?:off|descuentos?|descontos?|dto|rabatt\w*|discount\w*|rebaja\w*"
    r"|ahorr\w*|por\s+ciento|por\s+cento|procent|percent)(?![^\W\d_])"
)
_FREE_MONTHS = re.compile(
    r"\d+\s*(?:meses|mes|manad\w*|months?|dias|dagar|days)\s+(?:gratis|free)"
    r"|(?:gratis|free)\s+(?:por|durante|i|for|in|nos|em)\s+(?:(?:los|the|de|os)\s+)?"
    r"(?:primeros?\s+)?\d+"
)
_LIST_FILLER = _words("y e o ou och eller samt and or & el la los las the os as +")
_LIST_WORD = re.compile(r"[^\W\d_][\w+'’-]*|\d+|&")
# Ads markers (folded) after a plan word, or right after a service's name.
_ADS = (
    r"(?:con\s+anuncios|con\s+publicidad|con\s+avisos|com\s+anuncios|com\s+publicidade"
    r"|med\s+reklam\w*|with\s+ads|with\s+adverts|ad-supported|ads|reklam)"
)
_ADS_LEAD = re.compile(rf"^[\s,:(-]*{_ADS}(?![^\W\d_])")
_ADS_TAIL = re.compile(rf"\s+{_ADS}\s*$")
_PLAN_OF = re.compile(r"\s(?:de|del|do|da|of|fran|pa|for)\s*$")
# Generic plan words (folded) besides the catalog's: one right after a service's name that no
# plan of the service has is an unknown plan.
_GENERIC_PLAN_WORDS = _words(
    "basic basico basica standard estandar premium mobile movil lite ultimate essential "
    "esencial familiar family individual duo student pro padrao plus extra"
)
_ADS_WORDS = _words(
    "con com med with anuncios publicidad publicidade reklam ads adverts avisos ad-supported"
)


def _alias_written(name: str) -> frozenset[str] | None:
    """The spellings a case-sensitive alias must have, or None when case does not matter."""
    folded = _fold(name)
    if " " in folded or not folded.isalpha():
        return None
    if len(folded) > 3 and folded not in _COMMON_WORD_ALIASES:
        return None
    return frozenset({name, name.upper(), name[:1].upper() + name[1:].lower()})


@dataclass(frozen=True)
class _ServiceSpan:
    start: int  # raw offsets in the quote
    end: int
    fstart: int  # folded offsets
    fend: int
    service_id: str


def _service_spans(
    quote: str, folded: str, index: list[int], services: Sequence[ServiceDef]
) -> list[_ServiceSpan]:
    """Every mention of a service of `services` (longest name wins across services)."""
    owner: dict[str, tuple[str, str]] = {}
    for svc in services:
        for n in (svc.name, *svc.aliases):
            owner.setdefault(_fold(n), (svc.id, n))
    found: list[tuple[_ServiceSpan, bool]] = []
    for s, e, name in _mentions(folded, owner):
        service_id, original = owner[name]
        start, end = index[s], index[e - 1] + 1
        written = _alias_written(original)
        if written is not None and (
            quote[start:end] not in written or _ALIAS_BEFORE_NUMBER.match(quote, end)
        ):
            continue
        found.append((_ServiceSpan(start, end, s, e, service_id), written is not None))
    # A short alias right after another service's name is its plan ("Spotify Max").
    return [
        sp
        for k, (sp, short) in enumerate(found)
        if not (short and k > 0 and not quote[found[k - 1][0].end : sp.start].strip())
    ]


def _bare(clause: str, spans: Sequence[tuple[int, int]]) -> bool:
    """A bare list item: names and nothing else ("Universal+", "y ViX Premium", "HBO Max
    Estándar"), not "además HBO Max" nor "HBO Max incluido"."""
    rest = clause
    for s, e in sorted(spans, reverse=True):
        rest = rest[:s] + " " + rest[e:]
    words = [w for w in _LIST_WORD.findall(rest) if w.lower() not in _LIST_FILLER]
    return len(words) <= 3 and all(w[0].isupper() for w in words)


@dataclass
class _ComponentText:
    """A component quote cut into clauses, with the services and numbers of each."""

    quote: str
    folded: str
    clauses: _Clauses
    spans: list[_ServiceSpan]
    numbers: list[list[tuple[Decimal, int, int]]]  # per clause: (value, raw start, raw end)
    bare: list[bool]

    @classmethod
    def of(cls, quote: str, catalog: Catalog) -> _ComponentText:
        folded, index = _fold_map(quote)
        clauses = _Clauses.of(quote)
        spans = _service_spans(quote, folded, index, catalog.services)
        numbers: list[list[tuple[Decimal, int, int]]] = [[] for _ in range(len(clauses))]
        for n, s, e in _number_hits(quote):
            numbers[clauses.of_pos(s)].append((n, s, e))
        bare = []
        for k in range(len(clauses)):
            cs, ce = clauses.span(k)
            inside = [(sp.start - cs, sp.end - cs) for sp in spans if cs <= sp.start < ce]
            bare.append(_bare(quote[cs:ce], inside))
        return cls(quote, folded, clauses, spans, numbers, bare)

    def clause_text(self, k: int) -> str:
        s, e = self.clauses.span(k)
        return self.quote[s:e]

    def services_in(self, k: int) -> list[_ServiceSpan]:
        s, e = self.clauses.span(k)
        return [sp for sp in self.spans if s <= sp.start < e]

    def head(self, k: int) -> int:
        """The clause that heads the list clause k belongs to: k itself unless k is a bare item,
        else the nearest earlier clause of the sentence that is not one."""
        while k > 0 and self.bare[k] and not self.clauses.sentence[k]:
            k -= 1
        return k

    def number_clause(self, k: int, service_id: str) -> int | None:
        """The clause whose number belongs to the service mentioned in clause k (module
        docstring, component)."""
        if self.numbers[k]:
            return k
        j = k
        while j > 0 and self.bare[j] and not self.clauses.sentence[j]:
            j -= 1
            if self.numbers[j]:
                first = min(pos for _n, pos, _end in self.numbers[j])
                others = [sp for sp in self.services_in(j) if sp.service_id != service_id]
                if any(sp.start < first for sp in others):
                    return None  # "Disney+ con 30% OFF": Disney+'s number
                return j
        for j in range(k + 1, len(self.clauses)):
            if self.clauses.sentence[j]:
                return None
            if any(sp.service_id != service_id for sp in self.services_in(j)):
                return None
            if self.numbers[j]:
                return j
        return None

    def negated(self, sp: _ServiceSpan) -> bool:
        k = self.clauses.of_pos(sp.start)
        cs, _ce = self.clauses.span(k)
        own = _fold(self.clause_text(k))
        if _NEGATED.search(own) or _WITHOUT_BEFORE.search(_fold(self.quote[cs : sp.start])):
            return True
        h = self.head(k)
        if h != k:
            head = _fold(self.clause_text(h))
            if _NEGATED.search(head) or _WITHOUT_HEAD.search(head):
                return True
        # "Netflix y Disney+ no están incluidos": a negation after the list's last item.
        j = k
        while self.bare[j] and j + 1 < len(self.clauses) and not self.clauses.sentence[j + 1]:
            j += 1
            cs, _ce = self.clauses.span(j)
            text = self.clause_text(j)
            folded, index = _fold_map(text)
            if m := _NEGATED.search(folded):
                at = cs + index[m.start()]
                # "..., no incluye HBO Max" is about HBO Max only.
                return not self.services_in(j) or any(o.start < at for o in self.services_in(j))
        return False


def _plan_phrases(svc: ServiceDef) -> list[tuple[str, str, bool, bool]]:
    """(folded phrase, plan_id, ads plan, core) for every plan name of `svc`; an ads plan's name
    without its ads words is its "core" ("Basic with ads" -> "basic")."""
    out: list[tuple[str, str, bool, bool]] = []
    for p in svc.plans:
        ads = p.id.endswith("_with_ads")
        for n in dict.fromkeys([*p.aliases, p.label_es, p.label_en]):
            f = _fold(n)
            if not f:
                continue
            out.append((f, p.id, ads, False))
            if ads:
                core = re.sub(rf"\s*{_ADS}\s*", " ", f).strip()
                if core and core != f:
                    out.append((core, p.id, ads, True))
    return sorted(out, key=lambda t: -len(t[0]))


def _plan_words(catalog: Catalog) -> frozenset[str]:
    words: set[str] = set(_GENERIC_PLAN_WORDS)
    for svc in catalog.services:
        for p in svc.plans:
            for n in (*p.aliases, p.label_es, p.label_en):
                words.update(w for w in _fold(n).split() if w.isalpha() and w not in _ADS_WORDS)
    return frozenset(words)


def _resolve_plan(
    phrases: Sequence[tuple[str, str, bool, bool]], matched: str, ads_after: bool
) -> str | None:
    """The plan that `matched` (a phrase of `phrases`) names, with an ads marker after it or
    not; "unknown" for a plan word with an ads marker that no ads plan of the service has."""
    entries = [t for t in phrases if t[0] == matched]
    if ads_after:
        ads = [t for t in entries if t[2]]
        if ads:
            return ads[0][1]
        variants = [t for t in phrases if t[2] and t[3] and t[0] == matched]
        return variants[0][1] if variants else "unknown"
    full = [t for t in entries if not t[3]]
    plain = [t for t in full if not t[2]] or full
    return (plain or entries)[0][1]


def _starts_with(text: str, phrase: str) -> bool:
    return text.startswith(phrase) and not (
        len(text) > len(phrase) and text[len(phrase)].isalnum() and phrase[-1].isalnum()
    )


def _ends_with(text: str, phrase: str) -> bool:
    return text.endswith(phrase) and not (
        len(text) > len(phrase) and text[-len(phrase) - 1].isalnum() and phrase[0].isalnum()
    )


def _named_plan(
    svc: ServiceDef,
    phrases: Sequence[tuple[str, str, bool, bool]],
    plan_words: frozenset[str],
    folded: str,
    sp: _ServiceSpan,
    limit: int,
) -> str | None:
    """The plan of `svc` the text names at mention `sp` (module docstring, component): a plan
    id, "ads" (an ads plan, not which), "unknown", or None (no plan named)."""
    ads_plans = [p.id for p in svc.plans if p.id.endswith("_with_ads")]
    seg = re.sub(r"^[\s:(-]+", "", folded[sp.fend : limit])
    for phrase, _plan_id, _ads, _core in phrases:
        if _starts_with(seg, phrase):
            return _resolve_plan(phrases, phrase, bool(_ADS_LEAD.match(seg[len(phrase) :])))
    if _ADS_LEAD.match(seg):
        return ads_plans[0] if len(ads_plans) == 1 else "ads"
    first = re.match(r"[^\W\d_]+", seg)
    if first and first.group() in plan_words:
        return "unknown"
    # "el plan Básico con anuncios de HBO Max": the plan right before the name.
    lead = folded[max(0, sp.fstart - 60) : sp.fstart]
    if m := _PLAN_OF.search(lead):
        lead = lead[: m.start()].rstrip()
        tail = _ADS_TAIL.search(lead)
        core = lead[: tail.start()] if tail else lead
        for phrase, _plan_id, _ads, _core in phrases:
            if _ends_with(core, phrase):
                return _resolve_plan(phrases, phrase, tail is not None)
            if _ends_with(lead, phrase):
                return _resolve_plan(phrases, phrase, False)
    # Further on in the mention's segment ("Disney+ incluido en su plan Estándar").
    best: tuple[int, str] | None = None
    names: dict[str, list[str]] = {}
    for phrase, plan_id, _ads, core in phrases:
        if not core:
            names.setdefault(plan_id, []).append(phrase)
    for plan_id, ns in names.items():
        for s, e, _name in _mentions(seg, ns):
            if best is None or e - s > best[0]:
                best = (e - s, plan_id)
    return best[1] if best else None


def _check_component(
    value: object,
    quote: str,
    catalog: Catalog,
    *,
    context_lines: Sequence[str],
    bundle: Bundle | None,
    bundles: Iterable[Bundle] | None,
    country: str,
    unit: NumberUnit = "any",
) -> ValueCheck:
    """`unit` "pct": the value's number must be a percentage (kind discount_pct)."""
    raw_value = str(value).strip()
    service_id, _, number = raw_value.partition(":")
    if unit != "any" and not number:
        return _missing(f"value {raw_value[:40]!r} has no number (service_id:N)")
    service_id, _, plan_id = service_id.partition(".")
    try:
        svc = catalog.service(service_id)
    except KeyError:
        return _missing(f"unknown service {service_id[:40]!r}")
    if plan_id and svc.plan(plan_id) is None:
        return _missing(f"unknown plan {service_id}.{plan_id[:40]}")
    text, (qs, qe) = _context_text(quote, context_lines)
    if len(text) > MAX_CHECK_CHARS:
        return _too_long(len(text))
    q = text[qs:qe]
    ct = _ComponentText.of(q, catalog)
    own = [sp for sp in ct.spans if sp.service_id == svc.id]
    if not own:
        names = " / ".join(dict.fromkeys([svc.name, *svc.aliases]))
        return _missing(f"the quote does not name {names}")
    if any(ct.negated(sp) for sp in own):
        return _missing(f"the quote says {svc.name} is not included")
    clause_of = [ct.clauses.of_pos(sp.start) for sp in own]
    if number:
        n = _decimal(number)
        if n is None:
            return _missing(f"value {raw_value[:40]!r}: {number[:40]!r} is not a number")
        given = [j for k in clause_of if (j := ct.number_clause(k, svc.id)) is not None]
        same = [(s, e) for j in given for v, s, e in ct.numbers[j] if v == n]
        if not same:
            return _missing(f"the quote does not give {svc.name} the number {number}")
        if not any(_unit_ok(q, s, e, unit) for s, e in same):
            return _missing(f"the quote gives {svc.name} {number}, but not as a percentage")
    else:
        for k in clause_of:
            j = ct.number_clause(k, svc.id)
            for c in {k, *([j] if j is not None else [])}:
                body = _fold(ct.clause_text(c))
                if _DISCOUNT_MARK.search(body) or _FREE_MONTHS.search(body):
                    return _missing(
                        f"the quote gives {svc.name} a discount or free months, not an inclusion"
                    )
    if bundle is not None:
        if bundles is None:
            from haggler.catalog import default_bundles

            bundles = default_bundles().bundles
        siblings = [
            b
            for b in bundles
            if b.provider_id == bundle.provider_id
            and b.country == country
            and b.bundle_id != bundle.bundle_id
        ]
        tiers = _Tiers.of(text, "money", bundle, siblings)
        for sp in own:
            found = tiers.named(qs + sp.start, qs + sp.end)
            if isinstance(found, str):
                continue  # no tier named: component lines rarely name it
            named = {m.tier: m.printed for m in found}
            if len(named) > 1:
                return _missing(
                    f"the clause that names {svc.name}'s tier names several "
                    f"({', '.join(sorted(named.values()))})"
                )
            ((tier, printed),) = named.items()
            if tier != _OWN:
                return _missing(f"the tier named next to {svc.name} is {printed!r} ({tier})")
    if not plan_id:
        return _ok(f"names {svc.name}")
    phrases = _plan_phrases(svc)
    plan_words = _plan_words(catalog)
    named_plans: set[str | None] = set()
    for sp in own:
        nxt = min(
            (o.fstart for o in ct.spans if o.service_id != svc.id and o.fstart >= sp.fend),
            default=len(ct.folded),
        )
        named_plans.add(_named_plan(svc, phrases, plan_words, ct.folded, sp, nxt))
    if plan_id in named_plans:
        return _ok(f"names {svc.name} {plan_id}")
    is_ads_plan = plan_id.endswith("_with_ads")
    allowed: set[str | None] = {None, "ads"} if is_ads_plan else {None}
    if named_plans <= allowed:
        note = "a plan with ads" if "ads" in named_plans else "the plan is not named"
        return _ok(f"names {svc.name}; {note}")
    found_plans = sorted(p for p in named_plans if p and p != "ads")
    if "ads" in named_plans and not is_ads_plan:
        found_plans.append("a plan with ads")
    return _missing(f"the quote names {svc.name} {'/'.join(found_plans)}, not {plan_id}")


# ---- entry point ----------------------------------------------------------------------------


def _check_number(kind: ValueKind, value: object, quote: str) -> ValueCheck:
    number = _decimal(value)
    if number is None:
        return _missing(f"value {str(value)[:40]!r} is not a number")
    if len(quote) > MAX_CHECK_CHARS:
        return _too_long(len(quote))
    unit: NumberUnit = {"pct": "pct", "months": "months"}.get(kind, "int")  # type: ignore[assignment]
    if number in _numbers_in(quote, unit):
        return _ok(f"states {value}")
    what = {"int": "the count", "pct": "the percentage", "months": "the number of months"}[kind]
    return _missing(f"the quote does not state {what} {value}")


def _check_date(kind: ValueKind, value: object, quote: str) -> ValueCheck:
    try:
        wanted = value if isinstance(value, date) else date.fromisoformat(str(value).strip())
    except ValueError:
        return _missing(f"value {str(value)[:40]!r} is not an ISO date")
    if len(quote) > MAX_CHECK_CHARS:
        return _too_long(len(quote))
    found = find_dates(quote)
    same = [d for d in found if d.value == wanted]
    opposite = {"date_from": "until", "date_until": "from"}.get(kind)
    if any(opposite is None or date_direction(quote, d) != opposite for d in same):
        return _ok(f"states {wanted.isoformat()}")
    if same:
        given = "an end date" if opposite == "until" else "a start date"
        return _missing(f"the quote gives {wanted.isoformat()} as {given}")
    listed = ", ".join(d.value.isoformat() for d in found) or "no date with a year"
    return _missing(f"the quote does not state {wanted.isoformat()} (it states: {listed})")


def check_value(
    kind: ValueKind,
    value: object,
    quote: str,
    *,
    context_lines: Sequence[str] = (),
    country: str,
    currency: str,
    period: str | None = None,
    provider: Provider | None = None,
    bundle: Bundle | None = None,
    bundles: Iterable[Bundle] | None = None,
    catalog: Catalog | None = None,
    fx: FxTable | None = None,
    current: object = None,
    url: str | None = None,
) -> ValueCheck:
    """Whether `quote` states `value` (see the module docstring for each kind).

    `bundles` are the curated bundles the tier rule looks for the bundle's siblings in (same
    provider and country); None loads data/curated/bundles.yaml (haggler.catalog.
    default_bundles), the way `catalog` defaults to the service catalog. `context_lines` are
    the page lines around the quote (money kinds and the component tier rule). `url` is the
    page the quote comes from (money kinds; the url kind checks `value` itself); `current` is
    the YAML value a researched one would replace.
    """
    if kind in MONEY_KINDS:
        return _check_money(
            kind,
            value,
            quote,
            context_lines=context_lines,
            country=country,
            currency=currency,
            period=period,
            provider=provider,
            bundle=bundle,
            bundles=bundles,
            fx=fx,
            current=current,
            url=url,
        )
    if kind in NUMBER_KINDS:
        return _check_number(kind, value, quote)
    if kind in DATE_KINDS:
        return _check_date(kind, value, quote)
    if kind in ("component", "discount_pct"):
        if catalog is None:
            from haggler.catalog import default_catalog

            catalog = default_catalog()
        return _check_component(
            value,
            quote,
            catalog,
            context_lines=context_lines,
            bundle=bundle,
            bundles=bundles,
            country=country,
            unit="pct" if kind == "discount_pct" else "any",
        )
    if kind == "url":
        if provider is None:
            return _missing("no provider to check the url's domain against")
        from haggler.catalog import on_domains

        if on_domains(str(value), provider.domains.get(country, [])):
            return _ok(f"on a {country} domain of provider {provider.id}")
        return _missing(f"{value} is not https on a {country} domain of provider {provider.id}")
    raise ValueError(f"unknown value kind {kind!r}")
